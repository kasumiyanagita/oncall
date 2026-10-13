"""見学「まとめる」: 毎朝手で打っている確認を、スクリプトにする。

送信待ち(spool)と送信済み(sent)に、仕事のファイルが入っている。数えるのは、その個数。
"""

from __future__ import annotations

import random
from pathlib import Path

WORKDIR = "jobs"


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    counts = {"spool": rng.randint(12, 40), "sent": rng.randint(120, 300)}
    for name, count in counts.items():
        (root / name).mkdir(parents=True, exist_ok=True)
        for number in rng.sample(range(10000, 99999), count):
            (root / name / f"job-{number}.json").write_text(f'{{"id": {number}}}\n')
    return {"waiting": counts["spool"], "sent": counts["sent"]}
