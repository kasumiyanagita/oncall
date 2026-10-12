"""ミッション「同じ夜を繰り返さない」: ログの ERROR 行数を数えて警告するシェルスクリプトを書く。

プレイヤーの書いたスクリプトは、`check` のたびにシェルの中(隔離された側)で実行する。
`precheck()` が返すスクリプトがテスト用のログを作ってスクリプトを呼び、出力と終了コードを
~/.oncall-check/ に書き出す。`check()` はそれを読んで判定する。

ログには、レベルは INFO や WARN だが本文に「ERROR」という単語を含む行を混ぜてあり、
3 列目(レベル)を見ないと数え間違える。
"""

from __future__ import annotations

import os
import random
import shlex
from dataclasses import dataclass
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "tools"
SCRIPT = "errwatch.sh"
RESULT_DIR = ".oncall-check"

_ERROR_MESSAGES = (
    "payment gateway timeout order={n}",
    "database connection refused",
    "failed to write cache key=session:{n}",
    "upstream returned 502 path=/api/v1/orders/{n}",
)
_OTHER_LINES = (
    ("INFO", "request completed path=/api/v1/items/{n} ms={n}"),
    ("INFO", "cache hit key=item:{n}"),
    ("DEBUG", "pool stats active={n} idle=4"),
    ("WARN", "slow query took {n}ms"),
    # 本文に ERROR という単語を含むが、レベルは ERROR ではない行
    ("INFO", "recovered from ERROR state after retry"),
    ("WARN", "ERROR rate above 1% for 5 minutes"),
    ("INFO", "retry scheduled after ERROR response id={n}"),
)


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    args: tuple[str, ...]
    stdout: str | None  # None なら出力は問わない
    status: int


def _log(errors: int, others: int, rng: random.Random) -> str:
    entries = [("ERROR", rng.choice(_ERROR_MESSAGES)) for _ in range(errors)]
    # ひっかけの行は必ず入れる
    entries += [_OTHER_LINES[4], _OTHER_LINES[5], _OTHER_LINES[6]]
    entries += [rng.choice(_OTHER_LINES) for _ in range(others)]
    rng.shuffle(entries)
    lines = []
    second = 0
    for level, message in entries:
        second += rng.randint(1, 20)
        clock = f"{2 + second // 3600:02d}:{second % 3600 // 60:02d}:{second % 60:02d}"
        lines.append(f"2026-10-10 {clock} {level} {message.format(n=rng.randint(100, 9999))}")
    return "\n".join(lines) + "\n"


def _verdict(errors: int, threshold: int) -> tuple[str, int]:
    return (f"ALERT {errors}", 1) if errors > threshold else (f"OK {errors}", 0)


def generate(home: Path, rng: random.Random) -> dict:
    workdir = home / WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    sample_errors = rng.randint(25, 60)
    (workdir / "sample.log").write_text(_log(sample_errors, rng.randint(250, 400), rng))

    # check のたびに実行するテストケース。{dir} は実行時にログを置くディレクトリに置き換える
    high, low, equal, spaced = rng.randint(20, 40), rng.randint(3, 9), rng.randint(10, 19), rng.randint(11, 30)
    logs = {
        "high.log": _log(high, 60, rng),
        "low.log": _log(low, 60, rng),
        "equal.log": _log(equal, 60, rng),
        "quiet.log": _log(0, 40, rng),
        "night shift/app server.log": _log(spaced, 40, rng),
    }
    cases = [
        Case("high", f"ERROR が {high} 行、閾値 10", ("{dir}/high.log", "10"), *_verdict(high, 10)),
        Case("low", f"ERROR が {low} 行、閾値 20", ("{dir}/low.log", "20"), *_verdict(low, 20)),
        Case("equal", f"ERROR が {equal} 行、閾値も {equal}(超えていない)", ("{dir}/equal.log", str(equal)), *_verdict(equal, equal)),
        Case("quiet", "ERROR が 0 行、閾値 0", ("{dir}/quiet.log", "0"), *_verdict(0, 0)),
        Case("spaced", f"パスに空白を含むログ(ERROR {spaced} 行、閾値 5)", ("{dir}/night shift/app server.log", "5"), *_verdict(spaced, 5)),
        Case("nofile", "存在しないログファイルを指定", ("{dir}/no-such.log", "5"), None, 2),
        Case("noargs", "引数が 1 つしかない", ("{dir}/high.log",), None, 2),
    ]

    sample_out, _ = _verdict(sample_errors, 10)
    return {
        "logs": logs,
        "cases": cases,
        "sample_expected": sample_out,
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": "05:20",
    }


def precheck(ctx: dict) -> str:
    """シェルの中で実行する bash スクリプト。各ケースの出力と終了コードを書き出す。"""
    lines = [
        f'out="$HOME/{RESULT_DIR}"',
        'rm -rf "$out"',
        'mkdir -p "$out/logs"',
        f'script="$HOME/{WORKDIR}/{SCRIPT}"',
        'cd "$HOME" || exit',
    ]
    for rel, content in ctx["logs"].items():
        target = f'"$out/logs/{rel}"'
        lines.append(f'mkdir -p "$(dirname {target})"')
        lines.append(f"cat > {target} <<'ONCALL_EOF'\n{content}ONCALL_EOF")
    for case in ctx["cases"]:
        args = " ".join('"' + arg.replace("{dir}", "$out/logs") + '"' for arg in case.args)
        # 無限ループするスクリプトで check が固まらないよう、時間を区切る
        lines.append(f'timeout 5 "$script" {args} > "$out/{case.name}.out" 2> /dev/null < /dev/null')
        lines.append(f'echo $? > "$out/{case.name}.status"')
    return "\n".join(lines) + "\n"


def _usage(case: Case) -> str:
    """プレイヤーが同じ条件を手元で再現できるコマンド。テスト用のログは check のあとも残してある。"""
    args = " ".join(
        f"~/{RESULT_DIR}/logs/" + shlex.quote(arg.removeprefix("{dir}/")) if arg.startswith("{dir}/") else shlex.quote(arg)
        for arg in case.args
    )
    return f"./{SCRIPT} {args}"


def check(home: Path, ctx: dict) -> CheckResult:
    script = home / WORKDIR / SCRIPT
    if not script.is_file():
        return CheckResult(False, f"`~/{WORKDIR}/{SCRIPT}` がまだありません。")
    if not os.access(script, os.X_OK):
        return CheckResult(False, f"`{SCRIPT}` に実行権限がありません。`chmod +x {SCRIPT}` で付けられます。")
    if not script.read_text(errors="replace").startswith("#!"):
        return CheckResult(
            False,
            f"`{SCRIPT}` の 1 行目に `#!/bin/bash` を書いてください。どのプログラムで実行するかを示す行です。",
        )

    results = home / RESULT_DIR
    for case in ctx["cases"]:
        try:
            stdout = (results / f"{case.name}.out").read_text(errors="replace").strip()
            status = int((results / f"{case.name}.status").read_text().strip())
        except (OSError, ValueError):
            return CheckResult(False, "スクリプトの動作確認ができませんでした。もう一度 `check` を実行してください。")

        expected = f"終了コード {case.status}" if case.stdout is None else f"出力 `{case.stdout}`、終了コード {case.status}"
        if status == 124:
            actual = "5 秒たっても終わらなかった"
        elif case.stdout is None:
            actual = f"終了コード {status}"
        else:
            actual = f"出力 `{stdout or '(なし)'}`、終了コード {status}"
        if status != case.status or (case.stdout is not None and stdout != case.stdout):
            return CheckResult(
                False,
                f"次の場合の動作が仕様と違います。\n\n"
                f"条件: {case.description}\n\n実行: `{_usage(case)}`\n\n期待: {expected}\n\n実際: {actual}",
            )
    return CheckResult(True)
