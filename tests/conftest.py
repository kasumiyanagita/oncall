"""ミッションを実際のシェル環境で動かすためのテスト用ヘルパー。

隔離モードが使える環境では、本番と同じ隔離環境の中でスクリプトを実行する。
"""

import random
import subprocess
import time
from dataclasses import dataclass

import pytest

from oncall import procs, sandbox
from oncall.mission import CheckResult, load_missions, render

MISSIONS = {m.id: m for m in load_missions()}
MODE = sandbox.ISOLATED if sandbox.isolation_available() else sandbox.GUARD

needs_isolation = pytest.mark.skipif(
    MODE != sandbox.ISOLATED, reason="プロセスを扱うので、隔離モードが使える環境でだけ実行する"
)


@dataclass
class Played:
    ctx: dict
    result: CheckResult
    status: int  # スクリプトの終了コード


def play(mission_id: str, base, seed: int, script: str) -> Played:
    """ミッションを生成し、シェルの中で script を実行してから判定する。

    script の中の {{名前}} は、生成されたミッションの値に置き換える。
    """
    mission = MISSIONS[mission_id]
    layout = sandbox.Layout(base)
    layout.create()
    session = f"test{random.getrandbits(48):012x}"
    ctx = mission.scenario.generate(layout.home, random.Random(seed))
    ctx["session"] = session

    steps = []
    if setup := getattr(mission.scenario, "shell_setup", None):
        (layout.run / "setup.bash").write_text(setup(ctx))
        steps += ['. "$ONCALL_RUN/setup.bash"', "sleep 1"]
    (layout.run / "script.sh").write_text(render(script, ctx) + "\n")
    steps += ['cd "$ONCALL_WORKDIR" || exit', 'bash "$ONCALL_RUN/script.sh"', 'echo $? > "$ONCALL_RUN/status"']
    if precheck := getattr(mission.scenario, "precheck", None):
        (layout.run / "precheck.bash").write_text(precheck(ctx))
        steps.append('( . "$ONCALL_RUN/precheck.bash" ) > /dev/null 2>&1 < /dev/null')
    # 判定はプロセスが動いている間に行うので、合図を出してから待たせておく
    steps += ['touch "$ONCALL_RUN/done"', "exec sleep 120"]

    argv, env, cwd = sandbox.shell_command(
        MODE,
        layout,
        host=mission.host,
        workdir=mission.workdir,
        session=session,
        command=["bash", "-c", "\n".join(steps)],
    )
    shell = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 60
        while not (layout.run / "done").exists():
            assert shell.poll() is None, "シェルが途中で終了した"
            assert time.monotonic() < deadline, "スクリプトが終わらない"
            time.sleep(0.05)
        result = mission.scenario.check(layout.home, ctx)
        status = int((layout.run / "status").read_text())
    finally:
        shell.kill()
        shell.wait()
        procs.kill_session(session)
    return Played(ctx, result, status)


@pytest.fixture(autouse=True)
def _restore_permissions(tmp_path):
    """権限を外すテストのあとでも、pytest が一時ディレクトリを片付けられるようにする。"""
    yield
    sandbox.Layout(tmp_path).remove()
