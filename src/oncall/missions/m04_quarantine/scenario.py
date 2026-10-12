"""ミッション「壊れたファイルを隔離せよ」: 中身で絞り込んだ大量のファイルを xargs でまとめて移動する。

ファイル名に空白や ' を含むものを混ぜてあり、`xargs` を区切り指定なしで使うと取りこぼす。
CSV 以外(.log、.csv.tmp)にも同じ文字列を含むファイルがあり、拡張子で絞らないと巻き込む。
"""

from __future__ import annotations

import random
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "data"
MARKER = "checksum=INVALID"

_PLAIN_CUSTOMERS = ("globex", "initech", "umbrella", "hooli", "stark", "wayne")
_SPACED_CUSTOMERS = ("acme corp", "blue sky ltd", "north wind", "o'brien co", "d'angelo foods")
_KINDS = ("orders", "invoices", "returns", "payments")
_DAYS = ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09")


def _csv(valid: bool, rng: random.Random) -> str:
    rows = [f"{rng.randint(10000, 99999)},{rng.randint(1, 40)},{rng.randint(100, 99000)}" for _ in range(rng.randint(3, 9))]
    checksum = "VALID" if valid else "INVALID"
    return "\n".join(["id,quantity,amount", *rows, f"# rows={len(rows)} checksum={checksum}"]) + "\n"


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    (root / "quarantine").mkdir(parents=True, exist_ok=True)

    invalid: list[str] = []  # uploads からの相対パス
    others: list[str] = []
    serial = iter(rng.sample(range(1, 9999), 400))  # 通し番号で、ファイル名が全体で重複しないようにする

    def name(spaced: bool) -> str:
        if spaced:
            return f"{rng.choice(_SPACED_CUSTOMERS)} {rng.choice(_KINDS)} {next(serial):04d}"
        return f"{rng.choice(_PLAIN_CUSTOMERS)}_{rng.choice(_KINDS)}_{next(serial):04d}"

    def add(rel: str, content: str, *, is_invalid: bool) -> None:
        path = root / "uploads" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        (invalid if is_invalid else others).append(rel)

    for day in _DAYS:
        for _ in range(55):
            add(f"{day}/{name(rng.random() < 0.4)}.csv", _csv(True, rng), is_invalid=False)
        # 壊れた CSV。空白入り・' 入りの名前を必ず含める
        for spaced in (True, True, True, False, False, False, False, rng.random() < 0.5):
            add(f"{day}/{name(spaced)}.csv", _csv(False, rng), is_invalid=True)
        # CSV ではないが同じ文字列を含むファイル
        add(f"{day}/import.log", f"{day} 02:10:11 import finished\n{day} 02:10:12 3 files with {MARKER}\n", is_invalid=False)
        add(f"{day}/{name(False)}.csv.tmp", _csv(False, rng), is_invalid=False)
        add(f"{day}/README.txt", "アップロードされたファイルの置き場\n", is_invalid=False)
    # ' を含む名前の壊れた CSV を確実に 1 つ入れる
    add(f"{_DAYS[0]}/o'brien co returns {next(serial):04d}.csv", _csv(False, rng), is_invalid=True)

    return {
        "invalid": sorted(invalid),
        "others": sorted(others),
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": f"04:{rng.randint(10, 55)}",
    }


def check(home: Path, ctx: dict) -> CheckResult:
    root = home / WORKDIR
    uploads, quarantine = root / "uploads", root / "quarantine"
    if not quarantine.is_dir():
        return CheckResult(False, "`quarantine` ディレクトリがありません。`mkdir quarantine` で作り直してください。")

    expected = {Path(rel).name: rel for rel in ctx["invalid"]}
    in_quarantine = {p.name for p in quarantine.iterdir() if p.is_file()}
    extra = in_quarantine - set(expected)
    missing = set(expected) - in_quarantine

    nested = [p for p in quarantine.iterdir() if p.is_dir()]
    if nested:
        return CheckResult(False, "`quarantine` の直下にファイルを置いてください(日付のディレクトリごと移動しないでください)。")

    if extra:
        not_csv = sorted(n for n in extra if not n.endswith(".csv"))
        if not_csv:
            return CheckResult(
                False,
                f"CSV ではないファイルが `quarantine` に入っています(例: `{not_csv[0]}`)。"
                "対象は拡張子が `.csv` のファイルだけです。`uploads` に戻してください。",
            )
        return CheckResult(
            False,
            f"壊れていない CSV が `quarantine` に {len(extra)} 個入っています(例: `{sorted(extra)[0]}`)。`uploads` に戻してください。",
        )

    vanished = [rel for rel in ctx["others"] if not (uploads / rel).is_file()]
    vanished += [rel for name, rel in expected.items() if name in missing and not (uploads / rel).is_file()]
    if vanished:
        return CheckResult(
            False,
            f"`uploads` からなくなり、`quarantine` にもないファイルがあります(例: `{vanished[0]}`)。"
            "元には戻せないので、`exit` で抜けてやり直してください。",
        )

    if missing:
        # 一部だけ移動できている場合は、取りこぼしの原因になりやすい名前について知らせる
        tricky = [name for name in sorted(missing) if " " in name or "'" in name]
        note = ""
        if in_quarantine and tricky:
            note = (
                f"残っている中には、名前に空白や `'` を含むファイルがあります(例: `{tricky[0]}`)。"
                "`xargs` が名前をどこで区切っているか確認してください。"
            )
        return CheckResult(False, f"壊れた CSV がまだ `uploads` に {len(missing)} 個残っています。{note}")

    copied = [rel for rel in ctx["invalid"] if (uploads / rel).exists()]
    if copied:
        return CheckResult(False, f"`quarantine` には入っていますが、`uploads` にも {len(copied)} 個残っています。コピーではなく移動してください。")
    return CheckResult(True)
