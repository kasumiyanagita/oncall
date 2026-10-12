"""ミッション「止まらないワーカー」: 暴走しているプロセスを特定して止める。

シェルの起動時に、ワーカー 4 つと他のサービス 2 つをバックグラウンドで動かす。
ワーカーの 1 つは壊れたジョブを引いてリトライを繰り返しており、SIGTERM を無視する。
ほかのプロセスを巻き込まずに、その 1 つだけを止めるのが目標。
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from oncall import procs
from oncall.mission import CheckResult

WORKDIR = "services"
WORKER_IDS = (1, 2, 3, 4)
# (スクリプト名, 起動時の引数)
OTHER_SERVICES = (("api-server.sh", "--port 8080"), ("metrics-agent.sh", "--interval 5"))

WORKER_SCRIPT = r"""#!/bin/bash
# queue-worker: queue/worker-<id>.job に書かれたジョブを処理し続ける
# 使い方: ./queue-worker.sh --id <番号>
cd "$(dirname "$0")" || exit 1
id=$2
log() { echo "$(date '+%H:%M:%S') worker-$id $*" >> "logs/worker-$id.log"; }

job=$(cat "queue/worker-$id.job" 2>/dev/null)
if [[ $job == *payload=malformed* ]]; then
    # 壊れたジョブを引くと、成功するまでリトライし続けてしまう
    trap 'log "WARN  SIGTERM を受信したが、リトライ処理中のため無視"' TERM
    attempt=$(grep -c 'retrying' "logs/worker-$id.log")
    while true; do
        attempt=$((attempt + 1))
        log "ERROR ${job%% *} failed: malformed payload, retrying (attempt $attempt)"
        sleep 0.5
    done
fi

while true; do
    log "INFO  processed job=$((RANDOM + 10000))"
    sleep 3
done
"""

SERVICE_SCRIPT = r"""#!/bin/bash
# {name}: 定期的に動作状況を記録する
cd "$(dirname "$0")" || exit 1
while true; do
    echo "$(date '+%H:%M:%S') {name} INFO  {message}" >> "logs/{name}.log"
    sleep 5
done
"""


def _clock(seconds_ago: int) -> str:
    # 起動後はスクリプトが実際の時刻で書き足すので、過去のログも現在時刻から逆算する
    return f"{datetime.now() - timedelta(seconds=seconds_ago):%H:%M:%S}"


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "queue").mkdir(exist_ok=True)

    stuck = rng.choice(WORKER_IDS)
    for worker in WORKER_IDS:
        job_id = rng.randint(10000, 99999)
        payload = "malformed" if worker == stuck else "ok"
        (root / "queue" / f"worker-{worker}.job").write_text(f"job={job_id} payload={payload}\n")

        # 起動前から動いていたように、過去のログを用意する
        lines = [
            f"{_clock(900 - i * 30)} worker-{worker} INFO  processed job={rng.randint(10000, 99999)}"
            for i in range(20)
        ]
        if worker == stuck:
            # スクリプトはこの行数を数えて、続きの回数からリトライを再開する
            lines += [
                f"{_clock(280 - i)} worker-{worker} ERROR job={job_id} failed: malformed payload, retrying (attempt {i + 1})"
                for i in range(280)
            ]
        (root / "logs" / f"worker-{worker}.log").write_text("\n".join(lines) + "\n")

    scripts = {"queue-worker.sh": WORKER_SCRIPT}
    for (name, _), message in zip(OTHER_SERVICES, ("health check ok", "metrics flushed")):
        scripts[name] = SERVICE_SCRIPT.format(name=name.removesuffix(".sh"), message=message)
    for name, content in scripts.items():
        path = root / name
        path.write_text(content)
        os.chmod(path, 0o755)

    return {
        "stuck_id": stuck,
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": f"02:{rng.randint(41, 59)}",
    }


def shell_setup(ctx: dict) -> str:
    # setsid で端末から切り離し、Ctrl-C やジョブ制御の影響を受けないようにする
    lines = ['(', f'    cd "$HOME/{WORKDIR}" || exit']
    for worker in WORKER_IDS:
        lines.append(f"    setsid ./queue-worker.sh --id {worker} > /dev/null 2>&1 < /dev/null &")
    for name, args in OTHER_SERVICES:
        lines.append(f"    setsid ./{name} {args} > /dev/null 2>&1 < /dev/null &")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _running(session: str) -> tuple[set[int], set[str]]:
    """動いているワーカーの番号と、その他のサービスのスクリプト名。"""
    workers: set[int] = set()
    services: set[str] = set()
    for process in procs.session_processes(session):
        scripts = [Path(arg).name for arg in process.argv if arg.endswith(".sh")]
        if not scripts:
            continue
        if scripts[0] == "queue-worker.sh":
            if "--id" in process.argv and process.argv.index("--id") + 1 < len(process.argv):
                value = process.argv[process.argv.index("--id") + 1]
                if value.isdigit():
                    workers.add(int(value))
        else:
            services.add(scripts[0])
    return workers, services


def check(home: Path, ctx: dict) -> CheckResult:
    workers, services = _running(ctx["session"])
    stuck = ctx["stuck_id"]

    stopped_workers = sorted(set(WORKER_IDS) - {stuck} - workers)
    if stopped_workers:
        worker = stopped_workers[0]
        return CheckResult(
            False,
            f"正常に動いていたワーカー(worker-{worker})まで止まっています。"
            f"`./queue-worker.sh --id {worker} &` で起動し直してください。",
        )
    for name, args in OTHER_SERVICES:
        if name not in services:
            return CheckResult(
                False,
                f"関係のないサービス(`{name}`)まで止まっています。`./{name} {args} &` で起動し直してください。",
            )

    if stuck in workers:
        log = home / WORKDIR / "logs" / f"worker-{stuck}.log"
        if "SIGTERM" in log.read_text(errors="replace"):
            return CheckResult(
                False,
                "暴走しているワーカーがまだ動いています。止めようとした形跡はあるので、そのワーカーのログの末尾を見てみましょう。",
            )
        return CheckResult(False, "暴走しているワーカーがまだ動いています。")
    return CheckResult(True)
