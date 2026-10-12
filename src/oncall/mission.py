"""ミッション定義の読み込み。

1 ミッション = `missions/<ディレクトリ>/` で、中身は次の 2 ファイル。

- mission.toml : タイトル、チケット文、ヒント、解説などの文章データ
- scenario.py  : 作業ディレクトリの生成 `generate()` と正誤判定 `check()`

scenario.py には、必要に応じて次の関数も置ける(どちらも bash のスクリプト文字列を返す)。

- shell_setup(ctx) : シェルの起動時に、シェルの中で実行する準備(プロセスの起動など)
- precheck(ctx)    : `check` のたびに、判定の直前にシェルの中で実行する処理。
                     結果は ~/.oncall-check/ などに書き出し、`check()` がそれを読む
"""

from __future__ import annotations

import importlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from oncall.catalog import CommandEntry, load_catalog

MISSIONS_DIR = Path(__file__).parent / "missions"
DEFAULT_HINT_COSTS = (10, 15, 25)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class CheckResult:
    """scenario.check() の戻り値。message は不正解時にプレイヤーへ見せる一言。"""

    ok: bool
    message: str = ""


@dataclass(frozen=True)
class Hint:
    text: str
    cost: int


@dataclass(frozen=True)
class Alternative:
    command: str
    note: str
    # True なら依頼の一部だけを置き換える別解(単独では解決にならない)
    partial: bool = False


@dataclass(frozen=True)
class Mission:
    id: str
    title: str
    level: int
    host: str
    workdir: str
    par_time: int
    par_commands: int
    ticket: str
    hints: tuple[Hint, ...]
    solution: str
    explanation: str
    alternatives: tuple[Alternative, ...]
    # クリアすると図鑑に登録されるコマンド
    commands: tuple[CommandEntry, ...]
    scenario: ModuleType


def render(text: str, ctx: dict[str, Any]) -> str:
    """文章中の {{name}} を、scenario.generate() が返した値で置き換える。"""
    return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text)


def _load(directory: Path) -> Mission:
    with (directory / "mission.toml").open("rb") as f:
        data = tomllib.load(f)
    debrief = data["debrief"]
    catalog = load_catalog()
    hints = tuple(
        Hint(
            text=h["text"].strip(),
            cost=h.get("cost", DEFAULT_HINT_COSTS[min(i, len(DEFAULT_HINT_COSTS) - 1)]),
        )
        for i, h in enumerate(data.get("hints", []))
    )
    return Mission(
        id=data["id"],
        title=data["title"],
        level=data["level"],
        host=data["host"],
        workdir=data["workdir"],
        par_time=data["par_time"],
        par_commands=data["par_commands"],
        ticket=data["ticket"].strip(),
        hints=hints,
        solution=debrief["solution"].strip(),
        explanation=debrief["explanation"].strip(),
        alternatives=tuple(
            Alternative(a["command"].strip(), a["note"].strip(), a.get("partial", False))
            for a in debrief.get("alternatives", [])
        ),
        commands=tuple(catalog[name] for name in data.get("commands", [])),
        scenario=importlib.import_module(f"oncall.missions.{directory.name}.scenario"),
    )


def load_missions() -> list[Mission]:
    """ディレクトリ名の順(= プレイ順)に全ミッションを読み込む。"""
    return [
        _load(d)
        for d in sorted(MISSIONS_DIR.iterdir())
        if (d / "mission.toml").is_file()
    ]
