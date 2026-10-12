"""ミッション「置換ひとつで直るはずだった」: sed で複数の設定ファイルをまとめて修正する。

3 つの修正それぞれに、雑な書き方だと巻き込んでしまう行を混ぜてある。

- 1 行に db-old.internal が 2 回現れる行                  (g フラグの付け忘れ)
- 別のホスト db-old-internal.example.com                  (`.` をエスケープしない)
- connect_timeout / read_timeout、コメントの「# timeout」 (行頭 `^` を付けない)
- 「# debug = true は本番では禁止」というコメント          (行全体で一致させない)
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "app"
OLD_HOST = "db-old.internal"
NEW_HOST = "db-new.internal"
LOOKALIKE_HOST = "db-old-internal.example.com"
NEW_TIMEOUT = 30
DEBUG_COMMENT = "# debug = true は本番では禁止"

_FILES = (
    "conf/app.conf",
    "conf/db.conf",
    "conf/cache.conf",
    "conf/services/billing.conf",
    "conf/services/search.conf",
    "conf/services/notify.conf",
)


def _config(name: str, rng: random.Random, *, multi_host: bool, lookalike: bool, debug: bool, comment: bool) -> list[str]:
    service = Path(name).stem
    lines = [f"# {service} の設定", "", "[database]"]
    if multi_host:
        lines.append(f"primary = {OLD_HOST}:5432")
        lines.append(f"replicas = {OLD_HOST}:5433,{OLD_HOST}:5434")
    else:
        lines.append(f"host = {OLD_HOST}")
        lines.append("port = 5432")
    lines.append(f"connect_timeout = {rng.randint(2, 9)}")
    if lookalike:
        lines.append(f"metrics_endpoint = {LOOKALIKE_HOST}:9100")
    lines += ["", "[server]", f"workers = {rng.randint(2, 16)}"]
    lines.append(f"# timeout = {rng.randint(2, 9)}  (以前の値)")
    lines.append(f"timeout = {rng.choice([n for n in range(3, 121) if n != NEW_TIMEOUT])}")
    lines.append(f"read_timeout = {rng.randint(10, 90)}")
    lines += ["", "[logging]", f"level = {rng.choice(('info', 'warn'))}"]
    if comment:
        lines.append(DEBUG_COMMENT)
    if debug:
        lines.append("debug = true")
    lines.append(f"debug_sample_rate = 0.{rng.randint(1, 9)}")
    return lines


def _fixed(lines: list[str]) -> list[str]:
    """依頼どおりに修正した結果。"""
    result = []
    for line in lines:
        if line == "debug = true":
            continue
        if line.startswith("timeout = "):
            line = f"timeout = {NEW_TIMEOUT}"
        result.append(line.replace(OLD_HOST, NEW_HOST))
    return result


def generate(home: Path, rng: random.Random) -> dict:
    root = home / WORKDIR
    names = list(_FILES)
    multi = set(rng.sample(names, 3))
    lookalike = set(rng.sample(names, 2))
    debug = set(rng.sample(names, 4))
    comment = set(rng.sample(sorted(debug), 1) + rng.sample(names, 2))

    original: dict[str, list[str]] = {}
    for name in names:
        original[name] = _config(
            name,
            rng,
            multi_host=name in multi,
            lookalike=name in lookalike,
            debug=name in debug,
            comment=name in comment,
        )
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(original[name]) + "\n")

    # 壊したときに戻せるよう、デプロイ直後の状態を残しておく
    shutil.copytree(root / "conf", root / "conf.deployed")

    return {
        "original": original,
        "expected": {name: _fixed(lines) for name, lines in original.items()},
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": f"01:{rng.randint(10, 55)}",
    }


def check(home: Path, ctx: dict) -> CheckResult:
    root = home / WORKDIR
    actual: dict[str, list[str]] = {}
    for name in ctx["expected"]:
        path = root / name
        if not path.is_file():
            return CheckResult(
                False,
                f"`{name}` がありません。`cp -r conf.deployed/. conf/` でデプロイ直後の状態に戻せます。",
            )
        actual[name] = [line.rstrip() for line in path.read_text(errors="replace").splitlines()]

    if all(actual[name] == ctx["expected"][name] for name in actual):
        return CheckResult(True)

    restore = "やり直すときは `cp -r conf.deployed/. conf/` で元に戻せます。"
    every_line = [(name, line) for name, lines in actual.items() for line in lines]

    def count(predicate) -> int:
        return sum(1 for _, line in every_line if predicate(line))

    def lost(predicate) -> bool:
        """本来そのまま残るはずの行が、消えたり書き換わったりしているか。"""
        return any(
            line not in actual[name]
            for name, lines in ctx["expected"].items()
            for line in lines
            if predicate(line)
        )

    if lost(lambda line: LOOKALIKE_HOST in line):
        return CheckResult(
            False,
            f"`{LOOKALIKE_HOST}` は別のホストなので、変えてはいけません。"
            "正規表現の `.` は「任意の 1 文字」に一致します。文字としてのピリオドは `\\.` と書きます。" + restore,
        )
    if count(lambda line: OLD_HOST in line):
        return CheckResult(
            False,
            f"`{OLD_HOST}` がまだ {count(lambda line: OLD_HOST in line)} 行に残っています。"
            "1 行に 2 回現れる行や、サブディレクトリのファイルを見落としていませんか?",
        )
    if lost(lambda line: line.startswith(("connect_timeout = ", "read_timeout = ", "# timeout = "))):
        return CheckResult(
            False,
            "`connect_timeout` や `read_timeout`、コメント行まで書き換わっています。"
            "「行の先頭が timeout」のものだけに絞るには、パターンの先頭に `^` を付けます。" + restore,
        )
    if count(lambda line: line.startswith("timeout = ") and line != f"timeout = {NEW_TIMEOUT}"):
        return CheckResult(False, f"`timeout = {NEW_TIMEOUT}` になっていない timeout の行が残っています。")
    if lost(lambda line: line == DEBUG_COMMENT):
        return CheckResult(
            False,
            "コメント行まで削除されています。削除するのは、行全体が `debug = true` の行だけです。" + restore,
        )
    if count(lambda line: line == "debug = true"):
        return CheckResult(False, "`debug = true` の行がまだ残っています。行の削除は `sed '/パターン/d'` です。")

    name = next(name for name in actual if actual[name] != ctx["expected"][name])
    return CheckResult(
        False,
        f"`{name}` に、依頼にない変更が入っています。`diff -r conf.deployed conf` で差分を確認してください。" + restore,
    )
