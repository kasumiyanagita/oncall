"""見学「探す」: バックアップの置き場所から、条件に合うファイルを探す。

昨夜のデータベースのバックアップは、途中で失敗して小さいまま残っている。
数日前にも同じ失敗が 1 回あり、書きかけのファイル(.part)も残っている。
"""

from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

WORKDIR = "backup"
DAYS = 7
KINDS = ("db", "files")


def _write(path: Path, size: int, days_ago: int, rng: random.Random) -> None:
    path.write_bytes(rng.randbytes(size))
    # 毎晩 2 時ごろに取ったことにする(昨夜の分は 6 時間前)
    stamp = time.time() - (days_ago * 24 + 6) * 3600 + rng.randint(0, 600)
    os.utime(path, (stamp, stamp))


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    for kind in KINDS:
        (root / "archive" / kind).mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)

    today = datetime.now()
    earlier = rng.randint(3, DAYS - 1)  # 昨夜のほかに、失敗していた日
    failed = {0, earlier}
    log = []
    for days_ago in reversed(range(DAYS)):
        day = today - timedelta(days=days_ago)
        for kind in KINDS:
            name = f"{kind}-{day:%Y-%m-%d}.tar.gz"
            path = root / "archive" / kind / name
            log.append(f"{day:%Y-%m-%d} 02:00:01 INFO  start {kind}")
            if kind == "db" and days_ago in failed:
                _write(path, rng.randint(200, 900), days_ago, rng)
                _write(path.with_name(name + ".part"), rng.randint(20_000, 60_000), days_ago, rng)
                log.append(f"{day:%Y-%m-%d} 02:00:31 ERROR {kind}: connection to db-01 timed out after 30s")
            else:
                _write(path, rng.randint(40_000, 200_000), days_ago, rng)
                log.append(f"{day:%Y-%m-%d} 02:0{rng.randint(1, 9)}:{rng.randint(10, 59)} INFO  done {kind} {name}")
    (root / "logs" / "backup.log").write_text("\n".join(log) + "\n")
    return {"earlier_day": f"{today - timedelta(days=earlier):%Y-%m-%d}", "last_night": f"{today:%Y-%m-%d}"}
