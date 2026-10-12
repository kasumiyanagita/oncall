"""1 ミッション分のプレイ進行。

プレイヤーのシェルは擬似端末の上で動き、ゲーム本体は入出力を中継しながら、打たれた行を 1 行ずつ受け取る
(shell.py)。`mission`・`hint`・`check`・`zukan`・`title` は、シェルの中では何もしない関数で、
ゲーム本体が「その行が打たれた」ことを見て処理する。正解データはこのプロセスのメモリにだけ置き、
作業ディレクトリには書かない。
"""

from __future__ import annotations

import random
import secrets
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from oncall import design, procs, sandbox
from oncall.catalog import count_usage
from oncall.mission import CheckResult, Mission, render
from oncall.progress import ClearOutcome, Progress
from oncall.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from oncall.scoring import Score, evaluate
from oncall.screen import Screen
from oncall.shell import CommandResult, Shell
from oncall.zukan import Zukan

INDENT = "  "
# コマンド数に数えない、ゲーム用の合言葉
_HELPER_COMMANDS = {"mission", "hint", "check", "zukan", "title", "exit", "clear"}


def format_duration(seconds: float) -> str:
    """「7 分」「6 分 12 秒」の形。7:00 のように書くと時刻に見えるので、単位を付ける。"""
    minutes, secs = divmod(int(seconds), 60)
    if secs == 0 and minutes:
        return f"{minutes} 分"
    return f"{minutes} 分 {secs} 秒" if minutes else f"{secs} 秒"


def rank_markup(rank: str) -> str:
    """ランクを、そのランクの色で出すマークアップ。"""
    return f"[d.rank.{rank.lower()}]{rank}[/]"


@dataclass(frozen=True)
class Outcome:
    """プレイの終わり方。"""

    score: Score | None  # 解決していればそのスコア
    title: bool  # title と打って、トップページに戻ることを選んだ(False なら exit で終了を選んだ)


class MissionSession:
    def __init__(
        self,
        mission: Mission,
        console: Console,
        mode: str,
        progress: Progress,
        zukan: Zukan,
        seed: int | None = None,
        *,
        screen: Screen | None = None,
        input_fd: int | None = None,
        write=None,
        on_idle=None,
    ):
        self.mission = mission
        self.console = console
        # デザインの色の名前(d.〜)を、この Console で使えるようにする(呼び出し側が渡し忘れていても描ける)
        console.push_theme(design.theme())
        self.mode = mode
        self.progress = progress
        self.zukan = zukan
        self.screen = screen
        self.rng = random.Random(seed)
        self._input_fd = input_fd
        self._write = write
        self._on_idle = on_idle
        # このプレイのシェルと、そこから起動されたプロセスを見分ける合言葉
        self.token = secrets.token_hex(8)

        self.ctx: dict = {}
        self.layout: sandbox.Layout | None = None
        self.shell: Shell | None = None
        self.started = 0.0
        self.opened_hints = 0
        self.failed_checks = 0
        self.score: Score | None = None
        self.outcome: ClearOutcome | None = None
        self._commands: list[str] = []  # 実行されたコマンド(合言葉を含む)
        self._usage_saved: Counter[str] = Counter()
        self._to_title = False
        self._feedback = ""  # 直前の check への指摘(入力欄の下に出しておく)

    @property
    def _width(self) -> int:
        return min(self.console.width, MAX_WIDTH)

    def _prose(self, source: str, *, in_panel: bool = False):
        width = self._width - (PANEL_CHROME if in_panel else 0)
        return render_prose(render(source, self.ctx), width)

    # ---- 進行 ---------------------------------------------------------

    def play(self) -> Outcome:
        """ミッションを開始し、プレイヤーが title か exit を打つまで続ける。"""
        base = Path(tempfile.mkdtemp(prefix="oncall-"))
        try:
            self.layout = sandbox.Layout(base)
            self.layout.create()
            self.ctx = self.mission.scenario.generate(self.layout.home, self.rng)
            self.ctx["session"] = self.token
            self._prepare_run_dir()

            argv, env, cwd = sandbox.shell_command(
                self.mode,
                self.layout,
                host=self.mission.host,
                workdir=self.mission.workdir,
                session=self.token,
            )
            self._show_status()
            self._show_ticket(briefing=True)
            self.shell = Shell(
                argv,
                env,
                cwd,
                input_fd=self._input_fd,
                write=self._write,
                screen=self.screen,
                label=f"{sandbox.username()}@{self.mission.host}",
            )
            self.shell.on_idle = self._on_idle
            try:
                self.shell.run(self._on_command, on_ready=self._on_ready)
            finally:
                self.shell.close()
                self._sync_usage()
        finally:
            if self.screen is not None:
                self.screen.set_task(None)
            procs.kill_session(self.token)
            sandbox.Layout(base).remove()
        return Outcome(self.score, self._to_title)

    def _prepare_run_dir(self) -> None:
        run = self.layout.run
        for name in ("bashrc", "guard.bash"):
            shutil.copy(sandbox.SHELL_DIR / name, run / name)
        # ミッションが用意していれば、シェルの中で実行するスクリプトを置く(bashrc が読み込む)
        for filename, function in (("setup.bash", "shell_setup"), ("precheck.bash", "precheck")):
            if script := getattr(self.mission.scenario, function, None):
                (run / filename).write_text(script(self.ctx))
        # Ubuntu の /etc/bash.bashrc が出す sudo の案内文を抑える
        (self.layout.home / ".hushlogin").touch()

    def _on_ready(self) -> None:
        """シェルの準備ができた。ここから時間を計る。"""
        self.started = time.monotonic()
        self._refresh_task()

    def _on_command(self, result: CommandResult) -> bool:
        """プレイヤーが 1 行打つたびに呼ばれる。ミッションを離れるときに True を返す。"""
        # 出力が改行で終わっていないときは、案内が同じ行に続かないようにする
        if result.output and not result.output.endswith("\n"):
            self.console.print()
        self._commands.append(result.command)

        verb, _, argument = result.command.partition(" ")
        if verb == "title":
            self._to_title = True
            return True
        if verb == "mission":
            self._show_ticket()
        elif verb == "hint":
            self._hint()
        elif verb == "check":
            self._check()
        elif verb == "zukan":
            self._zukan(argument.strip())
        self._refresh_task()
        return False

    # ---- 入力欄の上下に出すもの ---------------------------------------

    def _show_status(self) -> None:
        """入力欄の上の線に、使える合言葉を出す。"""
        if self.screen is None:
            return
        self.screen.set_status(
            "[d.key]mission[/] 依頼",
            "[d.key]hint[/] ヒント",
            "[d.key]check[/] 判定",
            "[d.key]zukan[/] 図鑑",
            "[d.key]title[/] 戻る",
            "[d.key]exit[/] 終了",
        )

    def _refresh_task(self) -> None:
        """入力欄の下に、いま対応しているインシデントと、直前の判定への指摘を出したままにする。"""
        if self.screen is not None:
            self.screen.set_task(self._task_text)

    def _task_text(self, width: int) -> Text:
        look = design.active
        width = min(width, MAX_WIDTH)
        indent = f" {INDENT}"
        m = self.mission
        if self.score:
            heading = (
                f" [d.success]{look.CHECK} INCIDENT RESOLVED[/] [d.frame.caption]{m.title}[/]  "
                f"ランク {rank_markup(self.score.rank)} {self.score.points}pt"
            )
            guide = "このまま別解を試せます。`title` でトップページに戻ります。"
        else:
            heading = (
                f" [d.incident]{look.INCIDENT_MARK} INCIDENT[/] [d.frame.caption]{m.title}[/]  "
                f"[d.muted]目安 {format_duration(m.par_time)} / {m.par_commands} コマンド[/]"
            )
            guide = "`mission` で依頼をもう一度表示、`check` で解決したか判定します。"
        lines = [Text.from_markup(heading), render_prose(guide, width, indent=indent)]
        lines[1].stylize("d.muted")
        if self._feedback and not self.score:
            body = render_prose(f"{look.NOTE_MARK} {self._feedback}", width, indent=indent)
            body.stylize("d.hint")
            lines.append(body)
        return Text("\n").join(lines)

    # ---- 合言葉 -------------------------------------------------------

    def _show_ticket(self, briefing: bool = False) -> None:
        m = self.mission
        look = design.active
        body = Text(render(m.ticket, self.ctx))
        self.console.print()
        self.console.print(
            Panel(
                body,
                title=f"[d.incident]{look.INCIDENT_MARK} INCIDENT[/] [d.frame.caption]{m.title}[/]",
                title_align="left",
                subtitle=f"[d.muted]目安 {format_duration(m.par_time)} / {m.par_commands} コマンド[/]",
                box=look.BOX,
                border_style="d.frame",
                padding=(1, 2),
                width=self._width,
            )
        )
        if self.screen is None:
            # 画面を分けていないとき(テスト)は、使える合言葉をここに出す
            self.console.print(
                "[d.key]mission[/] 依頼を再表示   [d.key]hint[/] ヒントを開く(減点あり)   [d.key]check[/] 解決したか判定\n"
                "[d.key]zukan[/] 図鑑を見る   [d.key]title[/] トップページに戻る   [d.key]exit[/] 終了"
            )
        if briefing and self.mode == sandbox.GUARD:
            self.console.print(
                "[d.warn]簡易モード: このシェルは隔離されていません。作業は ~ の中だけで行ってください。[/]"
            )
        self.console.print()

    def _hint(self) -> None:
        hints = self.mission.hints
        newly_opened = self.opened_hints < len(hints)
        if newly_opened:
            self.opened_hints += 1

        self.console.print()
        for i, hint in enumerate(hints[: self.opened_hints], start=1):
            self.console.print(
                Panel(
                    self._prose(hint.text, in_panel=True),
                    title=f"[d.hint]ヒント {i}/{len(hints)}  (-{hint.cost}pt)[/]",
                    title_align="left",
                    box=design.active.BOX,
                    border_style="d.frame",
                    width=self._width,
                )
            )
        if not newly_opened:
            self.console.print("[d.muted]ヒントはこれで全部です。[/]")
        elif self.score:
            self.console.print("[d.muted]クリア済みなので減点はありません。[/]")
        self.console.print()

    def _check(self) -> None:
        self.console.print()
        if self.score:
            self.console.print("[d.done]このインシデントは解決済みです。[/] title でトップページに戻れます。\n")
            return

        result: CheckResult = self.mission.scenario.check(self.layout.home, self.ctx)
        if not result.ok:
            self.failed_checks += 1
            self._feedback = render(result.message, self.ctx).strip()
            self.console.print(f"[d.error]{design.active.CROSS} まだ解決していません[/]")
            self.console.print(self._prose(result.message))
            self.console.print()
            return

        m = self.mission
        self._feedback = ""
        self.score = evaluate(
            par_time=m.par_time,
            par_commands=m.par_commands,
            elapsed=time.monotonic() - self.started,
            commands=self._count_commands(),
            hint_costs=[h.cost for h in m.hints[: self.opened_hints]],
            failed_checks=self.failed_checks,
        )
        self.outcome = self.progress.record_clear(m.id, [c.name for c in m.commands], self.score)
        self._show_result()
        self._show_debrief()

    def _sync_usage(self) -> None:
        """前回からの増分だけ、コマンドの使用回数を進捗に足す。"""
        total = count_usage(self._commands, self.zukan.catalog)
        self.progress.add_usage(total - self._usage_saved)
        self._usage_saved = total

    def _zukan(self, query: str) -> None:
        self._sync_usage()
        if query:
            self.zukan.show_entry(self.console, query.split()[0])
        else:
            self.zukan.show_list(self.console)
            self.console.print("[d.muted]zukan <コマンド名> で詳細を表示します。[/]\n")

    def _count_commands(self) -> int:
        """合言葉を除いた、実行されたコマンドの数。"""
        return sum(1 for line in self._commands if line.split() and line.split()[0] not in _HELPER_COMMANDS)

    # ---- 結果表示 -----------------------------------------------------

    def _show_result(self) -> None:
        s, m = self.score, self.mission
        look = design.active

        stats = Table.grid(padding=(0, 2))
        stats.add_row("所要時間", format_duration(s.elapsed), f"[d.muted]目安 {format_duration(m.par_time)}[/]")
        stats.add_row("コマンド数", str(s.commands), f"[d.muted]目安 {m.par_commands}[/]")
        stats.add_row("ヒント", str(s.hints), "")
        stats.add_row("判定ミス", str(s.failed_checks), "")

        lines = [
            Text.from_markup(f"ランク {rank_markup(s.rank)}   {s.points} pt   {self._best_note()}"),
            Text(),
            stats,
        ]
        if s.penalties:
            lines.append(Text())
            lines.extend(Text(f"-{points:>2}  {label}", style="d.muted") for label, points in s.penalties)

        self.console.print(
            Panel(
                Group(*lines),
                title=f"[d.success]{look.CHECK} INCIDENT RESOLVED[/]",
                title_align="left",
                box=look.BOX,
                border_style="d.frame",
                padding=(1, 2),
                width=self._width,
            )
        )

    def _best_note(self) -> str:
        previous = self.outcome.previous_best
        if previous is None:
            return "[d.muted]初解決[/]"
        summary = f"{previous.rank} {previous.points}pt {format_duration(previous.elapsed)}"
        if self.outcome.is_best:
            return f"[d.new]自己ベスト更新[/] [d.muted](これまで {summary})[/]"
        return f"[d.muted]自己ベスト {summary}[/]"

    def _show_debrief(self) -> None:
        m, c = self.mission, self.console
        look = design.active
        c.print()
        c.print(Rule("[d.heading]振り返り[/]", style="d.rule", characters=look.RULE), width=self._width)
        c.print(f"\n[d.heading]{look.HEADING_MARK} 模範解答[/]")
        c.print(Syntax(render(m.solution, self.ctx), "bash", word_wrap=True, background_color="default"))
        c.print()
        c.print(self._prose(m.explanation))

        if m.alternatives:
            c.print(f"\n[d.heading]{look.HEADING_MARK} 別解[/]")
            for alt in m.alternatives:
                c.print(Syntax(render(alt.command, self.ctx), "bash", word_wrap=True, background_color="default"))
                c.print(self._prose(alt.note))
                c.print()

        if m.commands:
            c.print(f"[d.heading]{look.HEADING_MARK} このミッションのコマンド[/]")
            table = Table.grid(padding=(0, 2))
            for entry in m.commands:
                new = "[d.new]NEW[/]" if entry.name in self.outcome.new_commands else ""
                table.add_row(f"[d.command]{entry.name}[/]", entry.summary, new)
            c.print(table)
            if self.outcome.new_commands:
                c.print(f"\n[d.new]図鑑に {len(self.outcome.new_commands)} 個のコマンドを登録しました。[/]")
            c.print("[d.muted]zukan <コマンド名> でオプションや使用例を確認できます。[/]")

        c.print("\n[d.muted]このままシェルで別解を試せます。終わったら title でトップページに戻ります。[/]\n")
