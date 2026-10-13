"""見学「書き換える」: 調査のために詳しくした記録の設定(log_level)を、元に戻す。

`debug` という文字は、直したい行のほかにも出てくる。雑に置き換えると、関係のない行まで変わる。
"""

from __future__ import annotations

import random
from pathlib import Path

WORKDIR = "site"
SERVICES = ("api", "admin", "batch", "search")

CONF = """\
# {name}.conf
# log_level: error / warn / info / debug (debug は調査のときだけ)
listen = {port}
workers = {workers}
log_level = {level}
log_file = /var/log/site/{name}.log
debug_toolbar = off
"""


def generate(home: Path, rng: random.Random) -> dict:
    conf = home / WORKDIR / "conf"
    conf.mkdir(parents=True, exist_ok=True)

    # api は必ず debug のまま。ほかに 1〜2 個
    debug = {"api", *rng.sample(SERVICES[1:], rng.randint(1, 2))}
    for name in SERVICES:
        level = "debug" if name in debug else rng.choice(("info", "warn"))
        (conf / f"{name}.conf").write_text(
            CONF.format(name=name, port=rng.randint(8000, 8999), workers=rng.choice((2, 4, 8)), level=level)
        )
    return {"debug_count": len(debug), "file_count": len(SERVICES)}
