"""見学: 先輩が仕事をするところを、後ろから見せてもらう部屋。

メインのミッションとは別の、見るための場所。先輩が「なぜそのコマンドを打つのか」を話しながら、
短いコマンドを少しずつ長くしていく。コマンドは、本物のシェルで実際に実行される。
プレイヤーは Enter を押して先へ進めるだけで、打つのは先輩。採点はない。

ねらいは 2 つ。
- 長くて難しそうなコマンドも、小さな一歩を重ねて作っていることを見て、「自分にもできそうだ」と思えること
- 1 つずつのコマンドと、その組み合わせ方の理由を知ること

見学は、インシデントごとではなく「やり方」ごとに 1 本にする(数える、探す、比べる、…)。
主役は答えではなく、コマンドの組み立て方だからである。題材と環境は見学のために別に用意し、
インシデントの答えやヒントとは重ならないようにする。どのインシデントで使えるやり方かは、最後に案内する。

見学の中身は `tours/<名前>/` に置く。`tour.toml` に文章と先輩の手順を、`scenario.py` に環境を作る
`generate(home, rng)` を書く(ミッションと同じく、`shell_setup(ctx)` も置ける)。
"""

from __future__ import annotations

import importlib
import os
import random
import re
import secrets
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from oncall import design, procs, sandbox
from oncall.mission import Mission, render
from oncall.progress import Progress
from oncall.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from oncall.screen import RESIZED, Screen
from oncall.session import prepare_run_dir
from oncall.shell import CommandResult, Shell

TOURS_DIR = Path(__file__).parent / "tours"
WORD = "senpai"  # トップページで打つと、見学の部屋に入る言葉
INDENT = "  "
SENPAI = "senpai"  # 見学のあいだ、入力欄の先頭に出すユーザー名(先輩の端末を見ている、という印)
# 端末の制御文字列(色、カーソル移動など)。{{prev}} に入れる前に取り除く
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def plain(output: str) -> str:
    """端末に出た内容から制御文字列を取り除く。"""
    return _ANSI.sub("", output).replace("\r", "")


@dataclass(frozen=True)
class Step:
    say: str  # 打つ前に、先輩が話すこと(なぜ、それをするのか)
    run: str  # 先輩が打つコマンド
    note: str  # 打ったあとの解説(何が分かったか、コマンドのどこが何をしているか)


@dataclass(frozen=True)
class Tour:
    id: str
    technique: str  # やり方の名前(「数える」など)
    title: str  # 先輩が調べること
    commands: tuple[str, ...]  # 一覧に出す、登場するコマンド
    host: str
    workdir: str
    intro: str  # 始まる前に見せる、状況の説明
    steps: tuple[Step, ...]
    outro: str  # 見終えたあとのまとめ
    helps: tuple[Mission, ...]  # このやり方が使えるインシデント
    scenario: ModuleType  # 環境を作る scenario.py


def load_tours(missions: list[Mission]) -> list[Tour]:
    """ディレクトリ名の順に、全部の見学を読み込む。"""
    by_id = {mission.id: mission for mission in missions}
    tours = []
    for directory in sorted(TOURS_DIR.iterdir()):
        path = directory / "tour.toml"
        if not path.is_file():
            continue
        with path.open("rb") as f:
            data = tomllib.load(f)
        tours.append(
            Tour(
                id=data["id"],
                technique=data["technique"],
                title=data["title"],
                commands=tuple(data.get("commands", [])),
                host=data["host"],
                workdir=data["workdir"],
                intro=data["intro"].strip(),
                steps=tuple(Step(s["say"].strip(), s["run"].strip(), s["note"].strip()) for s in data["steps"]),
                outro=data["outro"].strip(),
                helps=tuple(by_id[mission_id] for mission_id in data.get("helps", [])),
                scenario=importlib.import_module(f"oncall.tours.{directory.name}.scenario"),
            )
        )
    return tours


@dataclass(frozen=True)
class Outcome:
    finished: bool  # 最後まで見た
    title: bool = False  # title と打って、トップページに戻ることを選んだ


class TourSession:
    """見学 1 本分の進行。"""

    def __init__(
        self,
        tour: Tour,
        console: Console,
        mode: str,
        progress: Progress,
        seed: int | None = None,
        *,
        screen: Screen | None = None,
        input_fd: int | None = None,
        write=None,
        on_idle=None,
    ):
        self.tour = tour
        self.console = console
        console.push_theme(design.theme())
        self.mode = mode
        self.progress = progress
        self.screen = screen
        self.rng = random.Random(seed)
        self._input_fd = input_fd
        self._write = write
        self._on_idle = on_idle
        self.token = secrets.token_hex(8)

        self.ctx: dict = {}
        self.shell: Shell | None = None
        self._index = 0  # いま見ている手
        self._phase = "before"  # before: これから打つ / running: 実行中 / after: 打ったあとの解説 / done: 見終えた
        self._to_title = False
        self._finished = False
        self._previous_output = ""  # 直前のコマンドの出力(次のコマンドの {{prev}} に入る)

    @property
    def _width(self) -> int:
        return min(self.console.width, MAX_WIDTH)

    def _text(self, source: str) -> str:
        """文章やコマンドの中の {{名前}} を置き換える。{{prev}} は、直前のコマンドの出力(調べた PID など)。"""
        return render(source, {**self.ctx, "prev": self._previous_output})

    # ---- 進行 ---------------------------------------------------------

    def play(self) -> Outcome:
        tour = self.tour
        base = Path(tempfile.mkdtemp(prefix="oncall-"))
        try:
            layout = sandbox.Layout(base)
            layout.create()
            self.ctx = tour.scenario.generate(layout.home, self.rng)
            self.ctx["session"] = self.token
            prepare_run_dir(layout, tour.scenario, self.ctx)
            argv, env, cwd = sandbox.shell_command(
                self.mode, layout, host=tour.host, workdir=tour.workdir, session=self.token
            )
            self._show_status()
            self._show_intro()
            self.shell = Shell(
                argv,
                env,
                cwd,
                input_fd=self._input_fd,
                write=self._write,
                screen=self.screen,
                label=f"{SENPAI}@{tour.host}",
            )
            self.shell.on_idle = self._on_idle
            self.shell.on_empty_enter = self._advance
            try:
                self.shell.run(self._on_command, on_ready=self._refresh)
            finally:
                self.shell.close()
        finally:
            if self.screen is not None:
                self.screen.set_task(None)
            procs.kill_session(self.token)
            sandbox.Layout(base).remove()
        return Outcome(self._finished, title=self._to_title)

    def _advance(self) -> None:
        """何も打たずに Enter が押された。次へ進める。"""
        if self._phase == "before":
            step = self.tour.steps[self._index]
            command = self._text(step.run)
            previous = self._text(self.tour.steps[self._index - 1].run) if self._index else ""
            # 前のコマンドの続きを書くときは、↑ で呼び出したように、同じ部分を一度に出す
            recalled = len(os.path.commonprefix([previous, command]))
            self._phase = "running"
            self._refresh()
            self.shell.type_and_run(command, recalled=recalled if recalled >= 8 else 0)
        elif self._phase == "after":
            self._index += 1
            if self._index == len(self.tour.steps):
                self._phase = "done"
                self._finished = True
                self.progress.mark_watched(self.tour.id)
            else:
                self._phase = "before"
            self._refresh()
        elif self._phase == "done":
            self.shell.stop()

    def _on_command(self, result: CommandResult) -> bool:
        """コマンドが 1 つ終わるたびに呼ばれる。先輩のコマンドなら解説へ進む。"""
        if result.output and not result.output.endswith("\n"):
            self.console.print()
        if result.command.split()[:1] == ["title"]:
            self._to_title = True
            return True
        if self._phase == "running":
            self._phase = "after"
            self._previous_output = plain(result.output).strip()
        self._refresh()
        return False

    # ---- 表示 ---------------------------------------------------------

    @property
    def _label(self) -> str:
        return f"[d.heading]{design.active.HEADING_MARK} 見学「{self.tour.technique}」[/]"

    def _show_status(self) -> None:
        if self.screen is None:
            return
        self.screen.set_status(
            "[d.key]Enter[/] 次へ",
            "[d.key]title[/] トップページに戻る",
            title="[d.heading]見学[/]",
        )

    def _show_intro(self) -> None:
        look = design.active
        inner = self._width - PANEL_CHROME - 2
        self.console.print()
        self.console.print(
            Panel(
                render_prose(self._text(self.tour.intro), inner),
                title=f"{self._label} [d.frame.caption]{self.tour.title}[/]",
                title_align="left",
                box=look.BOX,
                border_style="d.frame",
                padding=(1, 2),
                width=self._width,
            )
        )
        self.console.print()

    def _refresh(self) -> None:
        """入力欄の下に、先輩の話と解説を出したままにする。入力欄には、次に押すキーを薄く出す。"""
        if self.shell is not None:
            self.shell.placeholder = {
                "before": "Enter で、先輩がコマンドを打ちます",
                "running": "",
                "after": "Enter で次へ",
                "done": "Enter で見学の一覧に戻ります(このまま自分で打って試すこともできます)",
            }[self._phase]
        if self.screen is not None:
            self.screen.set_task(self._task_text)

    def _task_text(self, width: int) -> Text:
        width = min(width, MAX_WIDTH)
        indent = f" {INDENT}"
        total = len(self.tour.steps)
        number = min(self._index + 1, total)
        heading = Text.from_markup(f" {self._label} [d.muted]{number}/{total}[/]  [d.frame.caption]{self.tour.title}[/]")
        if self._phase == "done":
            outro = self._text(self.tour.outro)
            if self.tour.helps:
                # 題材は別でも、やり方はインシデントでそのまま使える。どこで使えるかを案内する
                titles = "".join(f"「{mission.title}」" for mission in self.tour.helps)
                outro += f"\n\n「{self.tour.technique}」のやり方は、インシデント{titles}で使えます。"
            return Text("\n").join([heading, render_prose(outro, width, indent=indent)])
        step = self.tour.steps[self._index]
        if self._phase in {"before", "running"}:
            body = render_prose("先輩「" + self._text(step.say) + "」", width, indent=indent)
        else:
            body = render_prose(self._text(step.note), width, indent=indent)
        return Text("\n").join([heading, body])


def _show_room(console: Console, tours: list[Tour], progress: Progress, missions: list[Mission]) -> None:
    look = design.active
    width = min(console.width, MAX_WIDTH)
    inner = width - PANEL_CHROME - 2
    table = Table(box=None, padding=(0, 1), header_style="d.muted", expand=True, pad_edge=False)
    table.add_column("No", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("やり方", no_wrap=True)
    table.add_column("先輩が調べること", no_wrap=True, overflow="ellipsis")
    table.add_column("出てくるコマンド", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("インシデント", no_wrap=True)
    for number, tour in enumerate(tours, start=1):
        table.add_row(
            f"[d.muted]{number}[/]",
            f"[d.done]{look.CHECK}[/]" if tour.id in progress.watched else " ",
            f"[d.heading]{tour.technique}[/]",
            tour.title,
            f"[d.command]{' '.join(tour.commands)}[/]",
            "[d.muted]" + " ".join(f"No.{missions.index(mission) + 1}" for mission in tour.helps) + "[/]",
        )
    about = render_prose(
        "先輩が仕事をするところを、後ろから見せてもらう部屋です。先輩は、なぜそのコマンドを打つのかを話しながら、"
        "短いコマンドを少しずつ長くしていきます。あなたは **Enter** を押して先へ進めるだけです。点数は付きません。\n\n"
        "見学は「やり方」ごとに 1 本です。インシデントとは別の題材ですが、同じやり方がインシデントでも使えます。",
        inner,
    )
    console.print()
    console.print(
        Panel(
            Group(about, Text(), table),
            title="[d.frame.title]見学: 先輩の作業を見る[/]",
            title_align="left",
            box=look.BOX,
            border_style="d.frame",
            padding=(1, 2),
            width=width,
        )
    )
    console.print(f"  [d.done]{look.CHECK}[/] [d.muted]最後まで見た[/]")


def room(
    screen: Screen,
    console: Console,
    tours: list[Tour],
    missions: list[Mission],
    progress: Progress,
    mode: str,
    seed: int | None = None,
) -> None:
    """見学の部屋。一覧から選んで見る。title でトップページに戻る(終了は、トップページでだけ選べる)。"""
    while True:
        screen.clear()
        _show_room(console, tours, progress, missions)
        unwatched = next((t for t in tours if t.id not in progress.watched), None)
        choices = [f"[d.key]Enter[/] 見学 {tours.index(unwatched) + 1}"] if unwatched else []
        screen.set_status(*choices, "[d.key]title[/] トップページに戻る", title="[d.heading]見学[/]")
        answer = screen.read_line(placeholder="Enter / 番号を入力(例: 1)", redraw_on_resize=True)
        if answer == RESIZED:
            continue
        if answer is None or answer.strip().lower() == "title":
            return
        answer = answer.strip().lower()
        if not answer and unwatched:
            tour = unwatched
        elif answer.isdigit() and 1 <= int(answer) <= len(tours):
            tour = tours[int(answer) - 1]
        else:
            continue
        screen.clear()
        outcome = TourSession(tour, console, mode, progress, seed, screen=screen).play()
        if outcome.title:
            return
