"""デザイン(通常とレトロ)が、互いに独立したまま同じ項目をそろえていること。"""

import io
from pathlib import Path

import pytest
from rich.console import Console

from oncall import cli, design, sandbox
from oncall.catalog import load_catalog
from oncall.designs import normal, retro
from oncall.mission import load_missions
from oncall.progress import Progress
from oncall.zukan import Zukan


@pytest.fixture(autouse=True)
def back_to_normal():
    yield
    design.use(normal.NAME)


def test_both_designs_define_the_same_items():
    design.check()


def test_designs_do_not_share_anything():
    """片方のファイルが、もう片方や共通の定義を読み込んでいないこと(変えても互いに影響しない)。"""
    for module in (normal, retro):
        source = Path(module.__file__).read_text()
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert imports == ["from rich import box"], imports
    assert normal.STYLES is not retro.STYLES


@pytest.mark.parametrize("name", sorted(design.DESIGNS))
@pytest.mark.parametrize("rows", [12, 21, 40])
def test_top_page_renders_in_each_design(name, rows, tmp_path):
    design.use(name)
    missions = load_missions()
    progress = Progress.load(tmp_path / "p.json")
    zukan = Zukan(load_catalog(), missions, progress)
    shown = set()
    page, pages = 0, 1
    while page < pages:
        out = io.StringIO()
        console = Console(file=out, theme=design.theme(), width=100, force_terminal=False)
        _, pages = cli._show_menu(console, rows, missions, progress, zukan, sandbox.ISOLATED, page)
        text = out.getvalue()
        assert "インシデント一覧" in text
        assert ("隔離モード" in text) == design.active.SHOW_MODE
        shown |= {m.id for m in missions if m.title in text}
        page += 1
    assert shown == {m.id for m in missions}  # どの大きさでも、ページを送れば全部が出る


def test_retro_uses_only_ascii_symbols_and_no_backslash_in_logo():
    symbols = [value for key, value in vars(retro).items() if key.isupper() and isinstance(value, str) and key != "NAME"]
    assert all(symbol.isascii() for symbol in symbols)
    assert "\\" not in retro.LOGO  # 日本語のフォントでは円記号で表示される
    assert not any("reverse" in style or " on " in style for style in retro.STYLES.values())
