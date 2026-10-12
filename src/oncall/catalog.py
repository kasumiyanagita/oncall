"""コマンド図鑑の元データ(commands.toml)の読み込みと、履歴からの使用回数の集計。"""

from __future__ import annotations

import functools
import re
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

CATALOG_FILE = Path(__file__).parent / "commands.toml"

# 1 行の中で新しいコマンドが始まる区切り
_COMMAND_BOUNDARY = re.compile(r"\|\||&&|\$\(|[|;`(\n]")


@dataclass(frozen=True)
class Option:
    flag: str
    description: str


@dataclass(frozen=True)
class Example:
    command: str
    note: str


@dataclass(frozen=True)
class CommandEntry:
    name: str
    summary: str
    syntax: str
    description: str
    options: tuple[Option, ...]
    examples: tuple[Example, ...]


@functools.cache
def load_catalog() -> dict[str, CommandEntry]:
    """コマンド名 → 図鑑の項目。順序は commands.toml に書かれた順。"""
    with CATALOG_FILE.open("rb") as f:
        data = tomllib.load(f)
    return {
        name: CommandEntry(
            name=name,
            summary=entry["summary"],
            syntax=entry["syntax"],
            description=entry["description"].strip(),
            options=tuple(Option(o["flag"], o["description"]) for o in entry.get("options", [])),
            examples=tuple(Example(e["command"].strip(), e["note"]) for e in entry.get("examples", [])),
        )
        for name, entry in data.items()
    }


def count_usage(history: Iterable[str], names: Iterable[str]) -> Counter[str]:
    """履歴の各行から、names に含まれるコマンドが何回実行されたかを数える。

    パイプや `;` で区切った各部分の先頭の単語を見るだけの簡易な集計。
    """
    known = set(names)
    usage: Counter[str] = Counter()
    for line in history:
        for part in _COMMAND_BOUNDARY.split(line):
            words = part.split()
            if words and (name := words[0].rsplit("/", 1)[-1]) in known:
                usage[name] += 1
    return usage


def commands_in(line: str) -> set[str]:
    """1 行の中で実行されるコマンド名。パイプや `;` で区切った各部分の先頭の語を見る。"""
    found = set()
    for part in _COMMAND_BOUNDARY.split(line):
        words = part.split()
        if words:
            found.add(words[0].rsplit("/", 1)[-1])
    return found
