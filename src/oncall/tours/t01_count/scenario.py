"""見学「数える」: メールの送信記録を、状態ごと、宛先のドメインごとに数える。

届かなかったメール(bounced)は、1 つのドメインに集中させてある。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

WORKDIR = "mail"
DOMAINS = ("example.com", "example.net", "example.org", "mail.example.jp", "corp.example.co.jp", "shop.example.jp")
RELAYS = ("mx1", "mx2", "mx3")
USERS = ("sato", "suzuki", "takahashi", "tanaka", "ito", "watanabe", "yamamoto", "nakamura", "kobayashi", "kato")


def generate(home: Path, rng: random.Random) -> dict:
    workdir = home / WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    bad = rng.choice(DOMAINS)
    start = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    lines = []
    for number in range(rng.randint(1800, 2400)):
        start += timedelta(seconds=rng.randint(1, 9))
        domain = rng.choice(DOMAINS)
        if domain == bad:
            status = rng.choices(("bounced", "sent"), (85, 15))[0]
        else:
            status = rng.choices(("sent", "deferred", "bounced"), (93, 5, 2))[0]
        lines.append(
            f"{start:%Y-%m-%d %H:%M:%S} id={rng.getrandbits(24):06X} "
            f"to={rng.choice(USERS)}{rng.randint(1, 99)}@{domain} relay={rng.choice(RELAYS)} status={status}"
        )
    (workdir / "mail.log").write_text("\n".join(lines) + "\n")
    return {"bad_domain": bad}
