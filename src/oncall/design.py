"""いま使うデザインの選択。画面を描くコードは、色や記号を design.active から取る。

デザインの中身は designs/normal.py と designs/retro.py にあり、互いに独立している。
"""

from __future__ import annotations

import os
from types import ModuleType

from rich.theme import Theme

from oncall.designs import normal, retro

DESIGNS: dict[str, ModuleType] = {normal.NAME: normal, retro.NAME: retro}
# レトロな見た目のタブの中で動いている印(Windows Terminal のプロファイルが付ける)
RETRO_VAR = "ONCALL_RETRO"

active: ModuleType = normal


def default_name() -> str:
    """指定がないときのデザイン。レトロな見た目のタブの中ならレトロ、それ以外は通常。"""
    return retro.NAME if os.environ.get(RETRO_VAR) else normal.NAME


def use(name: str) -> None:
    global active
    active = DESIGNS[name]


def theme() -> Theme:
    """いまのデザインの色を、Rich の Console に渡す形にしたもの。"""
    return Theme(active.STYLES)


def check() -> None:
    """2 つのデザインが、同じ項目を漏れなく定義しているかを確かめる(テスト用)。"""
    names = [{name for name in vars(module) if name.isupper()} for module in DESIGNS.values()]
    styles = [set(module.STYLES) for module in DESIGNS.values()]
    assert all(found == names[0] for found in names), "デザインによって定義している項目が違う"
    assert all(found == styles[0] for found in styles), "デザインによって定義している色の名前が違う"
