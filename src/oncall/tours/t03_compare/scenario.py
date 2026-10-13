"""見学「比べる」: 在籍者の名簿と、サーバーのアカウントの一覧を突き合わせる。

辞めた人のアカウントが残っていて、入ったばかりの人のアカウントがまだない。
"""

from __future__ import annotations

import random
from pathlib import Path

WORKDIR = "audit"
NAMES = (
    "abe", "aoki", "endo", "fujita", "goto", "hara", "hayashi", "ikeda", "inoue", "ishii", "ito", "kato",
    "kimura", "kondo", "maeda", "matsuda", "mori", "murata", "nakano", "nishi", "ogawa", "okada", "ono",
    "saito", "sakai", "sasaki", "shimizu", "takeda", "ueda", "wada", "yamada", "yoshida",
)
DEPARTMENTS = ("sales", "dev", "support", "ops", "finance")


def generate(home: Path, rng: random.Random) -> dict:
    workdir = home / WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    names = rng.sample(NAMES, 26)
    left = sorted(names[:3])  # 辞めた人(アカウントだけ残っている)
    joined = sorted(names[3:5])  # 入ったばかりの人(アカウントがまだない)
    both = names[5:]

    staff = both + joined
    rng.shuffle(staff)
    (workdir / "staff.txt").write_text(
        "".join(f"{name:<10}{rng.choice(DEPARTMENTS):<9}{rng.randint(2012, 2026)}\n" for name in staff)
    )
    accounts = both + left
    rng.shuffle(accounts)
    (workdir / "accounts.txt").write_text(
        "".join(f"{name}:x:{1001 + i}:{1001 + i}::/home/{name}:/bin/bash\n" for i, name in enumerate(accounts))
    )
    return {"left": "、".join(left), "joined": "、".join(joined), "left_count": len(left), "both_count": len(both)}
