"""見学「止める」: 日付を間違えて動かしてしまった集計を、1 つだけ止める。

同じスクリプトが 2 つ動いている。片方は正しい日付、もう片方は間違えた日付。
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

WORKDIR = "batch"

SCRIPT = """\
#!/bin/bash
# 毎晩の集計(見学用: 1 秒ごとに進み具合を記録するだけ)
day="$2"
n={start}
while true; do
    n=$((n + 1))
    echo "$(date +%H:%M:%S) date=$day processed chunk $n" >> "logs/report-$day.log"
    sleep 1
done
"""


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    (root / "logs").mkdir(parents=True, exist_ok=True)
    script = root / "nightly-report.sh"
    script.write_text(SCRIPT.format(start=rng.randint(200, 900)))
    os.chmod(script, 0o755)

    today = datetime.now()
    right = today - timedelta(days=1)
    wrong = right.replace(day=1) - timedelta(days=rng.randint(1, 3))  # 先月の終わりごろ
    return {"right_date": f"{right:%Y-%m-%d}", "wrong_date": f"{wrong:%Y-%m-%d}"}


def shell_setup(ctx: dict) -> str:
    # setsid で端末から切り離し、Ctrl-C やジョブ制御の影響を受けないようにする
    lines = ["(", f'    cd "$HOME/{WORKDIR}" || exit']
    for day in (ctx["right_date"], ctx["wrong_date"]):
        lines.append(f"    setsid ./nightly-report.sh --date {day} > /dev/null 2>&1 < /dev/null &")
    lines.append(")")
    return "\n".join(lines) + "\n"
