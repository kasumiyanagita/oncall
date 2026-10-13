"""コマンド図鑑の表示。トップページからも、ミッション中(`zukan`)からも使う。"""

from __future__ import annotations

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from oncall import design
from oncall.catalog import CommandEntry
from oncall.mission import Mission
from oncall.progress import Progress
from oncall.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from oncall.screen import RESIZED, Screen

# 項目パネルの余白(上下, 左右)。PANEL_CHROME は左右 1 を前提にしているので差分を引く
_PADDING = (1, 2)


class Zukan:
    def __init__(self, catalog: dict[str, CommandEntry], missions: list[Mission], progress: Progress):
        self.catalog = catalog
        self.missions = missions
        self.progress = progress

    @property
    def unlocked_count(self) -> int:
        return sum(1 for name in self.catalog if name in self.progress.unlocked)

    def show_list(self, console: Console) -> None:
        table = Table(box=None, padding=(0, 2), header_style="d.muted")
        table.add_column("No", justify="right")
        table.add_column("コマンド")
        table.add_column("説明")
        table.add_column("使った回数", justify="right")
        for number, entry in enumerate(self.catalog.values(), start=1):
            if entry.name in self.progress.unlocked:
                table.add_row(
                    str(number),
                    f"[d.command]{entry.name}[/]",
                    entry.summary,
                    str(self.progress.usage.get(entry.name, 0)),
                )
            else:
                table.add_row(str(number), "[d.muted]???[/]", "[d.muted]未登録[/]", "")
        console.print()
        console.print(f"[d.heading]{design.active.HEADING_MARK} コマンド図鑑[/]  {self.unlocked_count}/{len(self.catalog)}")
        console.print(table)
        console.print()

    def show_entry(self, console: Console, query: str) -> bool:
        """query(コマンド名か番号)の項目を表示する。表示できたら True。"""
        entry = self._find(query)
        if entry is None or entry.name not in self.progress.unlocked:
            console.print(
                f"\n[d.warn]「{escape(query)}」はまだ図鑑に登録されていません。[/]"
                "関連するインシデントを解決すると登録されます。\n"
            )
            return False

        width = min(console.width, MAX_WIDTH)
        inner = width - PANEL_CHROME - 2 * (_PADDING[1] - 1)
        parts: list = [
            Text(entry.summary, style="d.strong"),
            Text(),
            Text.assemble(("syntax  ", "d.muted"), (entry.syntax, "d.command")),
            Text(),
            render_prose(entry.description, inner),
        ]

        if entry.options:
            options = Table.grid(padding=(0, 2))
            for option in entry.options:
                options.add_row(Text(option.flag, style="d.command"), option.description)
            parts += [Text(), Text("よく使うオプション", style="d.muted"), options]

        if entry.examples:
            parts += [Text(), Text("使用例", style="d.muted")]
            for example in entry.examples:
                parts.append(Syntax(example.command, "bash", word_wrap=True, background_color="default"))
                parts.append(render_prose(example.note, inner, indent="  "))

        appears_in = [m.title for m in self.missions if any(c.name == entry.name for c in m.commands)]
        footer = f"登場: {'、'.join(appears_in)}   使った回数: {self.progress.usage.get(entry.name, 0)}"
        parts += [Text(), Text(footer, style="d.muted")]

        console.print()
        console.print(
            Panel(
                Group(*parts),
                title=f"[d.frame.command]{entry.name}[/]",
                title_align="left",
                box=design.active.BOX,
                border_style="d.frame",
                padding=_PADDING,
                width=width,
            )
        )
        console.print()
        return True

    def _find(self, query: str) -> CommandEntry | None:
        if query.isdigit():
            entries = list(self.catalog.values())
            return entries[int(query) - 1] if 1 <= int(query) <= len(entries) else None
        return self.catalog.get(query)

    def browse(self, console: Console, screen: Screen) -> None:
        """トップページから開く図鑑。コマンド名か番号を打つと詳細を出す。

        Enter か title でトップページに戻る(終了は、トップページでだけ選べる)。
        """
        shown = "list"  # いま表示しているもの(一覧か、項目の名前)
        screen.clear()
        self.show_list(console)
        while True:
            screen.set_status(
                "[d.key]Enter[/] トップページに戻る",
                "[d.key]list[/] 一覧をもう一度",
                title="コマンド図鑑",
            )
            answer = screen.read_line(placeholder="コマンド名か番号を入力(例: grep)", redraw_on_resize=True)
            if answer == RESIZED:  # 端末の大きさが変わった。同じものを、新しい大きさで描き直す
                answer = shown
            if answer is None or answer.strip() in {"", "title"}:
                return
            answer = answer.strip()
            if answer == "exit":  # 終了は、トップページでだけ選べる
                screen.exit_refused(console)
                continue
            shown = answer
            screen.clear()
            if answer == "list":
                self.show_list(console)
            else:
                self.show_entry(console, answer)
