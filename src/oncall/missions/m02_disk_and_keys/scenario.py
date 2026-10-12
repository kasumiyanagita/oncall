"""ミッション「満杯のディスクと開いた鍵」: find で条件に合うファイルを探して削除し、鍵の権限を直す。

削除の条件は「10MB 超」かつ「7 日より前から更新されていない」。片方だけ満たすファイル
(大きいが新しい / 古いが小さい)を混ぜてあり、条件を 1 つしか見ないと消しすぎる。
"""

from __future__ import annotations

import os
import random
import stat
import time
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "srv"
MB = 1024 * 1024
DAY = 24 * 60 * 60
KEY_MODE = 0o600

# (パス, 古くて大きい不要ファイルになりうるか) の候補。ここから 3 つを削除対象に選ぶ
_JUNK_CANDIDATES = (
    "backup/dump-full-{old_date}.sql",
    "backup/archive/orders-export-{old_date}.csv",
    "logs/debug-trace.log.1",
    "logs/archive/batch-{old_date}.log",
    "tmp/core.{n}",
    "tmp/upload-{n}.part",
    "app/releases/build-cache-{n}.tar",
)
_KEY_PATHS = (
    "certs/api-gateway.pem",
    "certs/internal-ca.pem",
    "certs/legacy/old-site.pem",
    "deploy/keys/deploy.pem",
    "deploy/keys/rollback.pem",
    "app/current/secrets/jwt-signing.pem",
)
_SMALL_FILES = (
    ("certs/api-gateway.crt", 0o644),
    ("certs/internal-ca.crt", 0o644),
    ("certs/legacy/old-site.crt", 0o644),
    ("deploy/deploy.sh", 0o755),
    ("deploy/rollback.sh", 0o755),
    ("deploy/README.md", 0o644),
    ("app/current/config.yml", 0o644),
    ("app/current/app.py", 0o644),
    ("app/current/run.sh", 0o755),
    ("app/current/secrets/README.md", 0o640),
    ("app/releases/RELEASES.txt", 0o644),
    ("logs/app.log", 0o644),
    ("logs/app.log.1", 0o644),
    ("logs/app.log.2", 0o644),
    ("logs/archive/batch-summary.txt", 0o644),
    ("backup/README.md", 0o644),
    ("backup/archive/manifest.txt", 0o644),
    ("tmp/session-a81f.tmp", 0o600),
    ("tmp/session-c02d.tmp", 0o600),
    ("data/schema.sql", 0o644),
)


def _write(path: Path, size: int, mode: int, age_days: float, rng: random.Random) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 疎なファイルにすると du と ls で大きさが食い違うので、実際に中身を書く
    path.write_bytes(bytes([rng.randrange(256)]) * size)
    os.chmod(path, mode)
    mtime = time.time() - age_days * DAY
    os.utime(path, (mtime, mtime))


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    root.mkdir(parents=True, exist_ok=True)

    junk: list[str] = []
    for template in rng.sample(_JUNK_CANDIDATES, 3):
        age = rng.randint(10, 120)
        old_date = time.strftime("%Y%m%d", time.localtime(time.time() - age * DAY))
        rel = template.format(old_date=old_date, n=rng.randint(1000, 9999))
        _write(root / rel, rng.randint(11, 14) * MB, 0o644, age, rng)
        junk.append(rel)

    keep: dict[str, int] = {}

    def add(rel: str, size: int, mode: int, age_days: float) -> None:
        _write(root / rel, size, mode, age_days, rng)
        keep[rel] = mode

    # 大きいが新しい(稼働中)
    add("data/orders.db", rng.randint(11, 13) * MB, 0o644, rng.uniform(0.01, 0.5))
    add("backup/dump-full-latest.sql", rng.randint(11, 13) * MB, 0o644, rng.uniform(1, 3))
    # 古いが 10MB 以下
    add("logs/app.log.3", rng.randint(3, 6) * MB, 0o644, rng.randint(20, 60))
    add("backup/archive/config-snapshot.tar", rng.randint(1, 4) * MB, 0o644, rng.randint(30, 200))
    for rel, mode in _SMALL_FILES:
        add(rel, rng.randint(200, 90_000), mode, rng.choice((0.2, 2, 5, 15, 40, 100)))

    # 秘密鍵: 2 つは最初から正しく、残りは開きすぎている
    wrong_modes = [0o644, 0o666, 0o640, 0o664]
    rng.shuffle(wrong_modes)
    key_modes = [KEY_MODE, KEY_MODE, *wrong_modes]
    rng.shuffle(key_modes)
    for rel, mode in zip(_KEY_PATHS, key_modes):
        _write(root / rel, rng.randint(1600, 3300), mode, rng.randint(5, 300), rng)

    dir_modes = {}
    for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        os.chmod(directory, 0o755)
        dir_modes[str(directory.relative_to(root))] = 0o755

    return {
        "junk": sorted(junk),
        "keep": keep,
        "dirs": dir_modes,
        "keys": list(_KEY_PATHS),
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": f"03:{rng.randint(10, 55)}",
    }


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _changed_directory(root: Path, dirs: dict[str, int]) -> tuple[str, int] | None:
    """権限が変わったディレクトリを、上の階層から順に探す。"""
    for rel, mode in sorted(dirs.items(), key=lambda item: len(Path(item[0]).parts)):
        try:
            if _mode(root / rel) != mode:
                return rel, mode
        except OSError:  # 親ディレクトリに入れなくなっている
            return rel, mode
    return None


def check(home: Path, ctx: dict) -> CheckResult:
    root = home / WORKDIR

    # ディレクトリに入れなくなっていると中のファイルを調べられないので、最初に確かめる
    if changed_dir := _changed_directory(root, ctx["dirs"]):
        rel, mode = changed_dir
        return CheckResult(
            False,
            f"ディレクトリの権限が変わっています(例: `{rel}` は元は {mode:o})。"
            "ディレクトリから実行権限を外すと、中に入れなくなります。"
            f"`find . -type d -exec chmod {mode:o} {{}} +` でディレクトリを元に戻し、`*.pem` だけを対象にしてください。",
        )

    lost = [rel for rel in [*ctx["keep"], *ctx["keys"]] if not (root / rel).is_file()]
    if lost:
        return CheckResult(
            False,
            f"消してはいけないファイルがなくなっています(例: `{lost[0]}`)。"
            "新しいファイルや小さいファイルは稼働中のサービスが使っています。"
            "元には戻せないので、`exit` で抜けてやり直してください。",
        )

    remaining = [rel for rel in ctx["junk"] if (root / rel).exists()]
    wrong_keys = [rel for rel in ctx["keys"] if _mode(root / rel) != KEY_MODE]

    changed = [(rel, mode) for rel, mode in ctx["keep"].items() if _mode(root / rel) != mode]
    if changed:
        rel, mode = changed[0]
        return CheckResult(
            False,
            f"鍵以外の権限が変わっています(例: `{rel}` は元は {mode:o})。"
            "`*.pem` だけを対象にして、ほかは元の権限に戻してください。",
        )

    if remaining and wrong_keys:
        return CheckResult(
            False,
            f"依頼 1: 条件に合うファイルがまだ {len(remaining)} 個残っています。"
            f"依頼 2: 権限が 600 でない鍵が {len(wrong_keys)} 個あります。",
        )
    if remaining:
        return CheckResult(
            False,
            f"鍵の権限は直りました。依頼 1 が残っています。"
            f"「10MB 超」かつ「7 日より前から更新なし」のファイルがまだ {len(remaining)} 個あります。",
        )
    if wrong_keys:
        return CheckResult(
            False,
            f"ディスクは片付きました。依頼 2 が残っています。権限が 600 でない `*.pem` が {len(wrong_keys)} 個あります。",
        )
    return CheckResult(True)
