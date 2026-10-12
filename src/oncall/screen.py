"""画面の割り付け: 上から「表示」「入力欄」「ステータスバー」に分ける。

端末のスクロール範囲を表示の部分だけに絞り、その下に入力欄とステータスバーを描く。
入力欄の上の罫線には、左端にいまいる場面(開いているミッション)、右端に使える操作を出す。
ステータスバーには、いま取り組んでいるお題だけを出す(お題がなければ何も出さない)。
表示の部分は普通の端末と同じように流れるので、Rich の出力もシェルの出力もそのまま書ける。

カーソルは 1 つしかないので、入力を待つあいだだけ入力欄に置き(park)、表示へ書く前に
元の位置へ戻す(unpark)。
"""

from __future__ import annotations

import os
import re
import select
import signal
import sys
import termios
from collections.abc import Callable

from rich.console import Console
from rich.text import Text

from oncall import design
from oncall.lineedit import EOF, INTERRUPT, SUBMIT, LineEditor

CHROME = 3  # 罫線・入力欄・罫線
MIN_ROWS = 5  # お題が長くても、表示の部分にこの行数は残す
_SAVE, _RESTORE = "\x1b7", "\x1b8"
# カーソルの形を端末の設定に戻す指定(DECSCUSR)。入力欄での形は、デザインが決める(design.active.CURSOR)
_DEFAULT_CURSOR = "\x1b[0 q"
# 画面を消す・別画面へ切り替える・スクロール範囲を変える出力。入力欄とステータスバーが
# 消えたり崩れたりするので、このあと描き直す
_DAMAGE = re.compile(rb"\x1b\[[0-9;?]*J|\x1b\[\?[0-9;]*(?:47|1047|1049)[0-9;]*[hl]|\x1b\[[0-9;]*r|\x1bc")

# read_line が、端末の大きさが変わったことを知らせるために返す値(キー入力では作れない文字列)
RESIZED = "\x00resized"
MENU_PROMPT = Text("> ", style="d.prompt.menu")
# 表示の部分に残す、確定した入力の行。背景を付けて、どこからが新しい結果なのかを見分けやすくする
ECHO_STYLE = "d.echo"


class Screen:
    def __init__(self, console: Console):
        self.console = console
        self.input_fd = sys.stdin.fileno()
        # 端末の大きさが変わったあとに呼ばれる(シェルに新しい大きさを伝えるのに使う)
        self.on_resize: Callable[[], None] | None = None
        self._active = False
        self._parked = False
        self._status = Text()
        self._title = Text()
        self._idle = Text()  # 入力を待っていないあいだ、入力欄に出しておく一言
        self._placeholder = ""  # 入力を待っていて、まだ何も打たれていないあいだ、入力欄に薄く出しておく案内
        self._task: Callable[[int], Text] | None = None
        self._task_lines: list[str] = []
        self._prompt = Text()
        self._editor = LineEditor()
        self._lines, self._columns = 24, 80

    # ---- 開始と終了 ---------------------------------------------------

    def __enter__(self) -> Screen:
        # 打鍵を 1 文字ずつ受け取る。出力側の設定は変えない
        self._saved = termios.tcgetattr(self.input_fd)
        attrs = termios.tcgetattr(self.input_fd)
        attrs[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.input_fd, termios.TCSADRAIN, attrs)

        # 端末の大きさが変わったことは、シグナルの中では描き直さず、入力待ちのループへ知らせる
        self.wake_fd, self._wake_write = os.pipe()
        os.set_blocking(self.wake_fd, False)
        os.set_blocking(self._wake_write, False)
        self._old_wakeup = signal.set_wakeup_fd(self._wake_write, warn_on_full_buffer=False)
        self._old_winch = signal.signal(signal.SIGWINCH, lambda *_: None)

        self._measure()
        # いま画面にある内容を上へ送り出してから、表示の部分の先頭に立つ
        self._out("\n" * self._lines + "\x1b[H" + design.active.CURSOR)
        self._active = True
        self._layout()
        return self

    def __exit__(self, *_: object) -> None:
        self.unpark()
        self._active = False
        self._out(f"{_SAVE}\x1b[r\x1b[{self.rows + 1};1H\x1b[J{_RESTORE}" + (_DEFAULT_CURSOR if design.active.CURSOR else ""))
        signal.signal(signal.SIGWINCH, self._old_winch)
        signal.set_wakeup_fd(self._old_wakeup)
        os.close(self.wake_fd)
        os.close(self._wake_write)
        termios.tcsetattr(self.input_fd, termios.TCSADRAIN, self._saved)

    # ---- 大きさ -------------------------------------------------------

    def _terminal_size(self) -> tuple[int, int]:
        try:
            return tuple(os.get_terminal_size(sys.stdout.fileno()))
        except OSError:
            return self._columns, self._lines

    def _measure(self) -> None:
        self._columns, self._lines = self._terminal_size()

    @property
    def rows(self) -> int:
        """表示の部分の行数。"""
        return max(1, self._lines - CHROME - len(self._task_lines))

    @property
    def size(self) -> tuple[int, int]:
        """表示の部分の (行数, 桁数)。シェルにはこの大きさの端末として見せる。"""
        return self.rows, self._columns

    def check_resize(self) -> bool:
        """wake_fd が読めるようになったら呼ぶ。端末の大きさが変わっていたら割り付け直し、True を返す。"""
        try:
            os.read(self.wake_fd, 1024)
        except BlockingIOError:
            pass
        if self._terminal_size() == (self._columns, self._lines):
            return False
        self._reflow(self._measure)
        return True

    def _reflow(self, change: Callable[[], None]) -> None:
        """表示の部分の大きさが変わりうる変更(端末の大きさ、お題)を行い、割り付け直す。"""
        parked = self._parked
        self.unpark()
        old_size, old_lines, old_rows = self.size, self._lines, self.rows
        change()
        # お題は幅に合わせて折り返し直す
        text = self._task(self._columns - 1) if self._task else None
        lines = text.split("\n") if text else []
        self._task_lines = [self._ansi(line) for line in lines[: max(0, self._lines - CHROME - MIN_ROWS)]]

        if self._active and self._lines != old_lines:
            # 端末の大きさが変わった。前の位置に残った入力欄とステータスバーを消す
            stale = f"\x1b[{old_rows + 1};1H\x1b[J" if old_rows < self._lines else ""
            self._out(f"{_SAVE}\x1b[r{stale}{_RESTORE}")
            self._layout()
            if self._lines < old_lines:
                # 縮んだときはカーソルが表示の部分の外に出ていることがあるので、末尾の空行へ移す
                self._out(f"\x1b[{self.rows};1H\n")
        elif self._active:
            if self.rows < old_rows:
                # 表示の部分が狭くなる。カーソルが隠れる行にあるときだけ、その分の中身を上へ送る
                # (下へ進めて末尾に当たった分だけ流れ、そのあと同じ行数を戻る)
                hidden = old_rows - self.rows
                self._out("\x1bD" * hidden + f"\x1b[{hidden}A")
            elif self.rows > old_rows:
                self._out(f"{_SAVE}\x1b[{old_rows + 1};1H\x1b[J{_RESTORE}")
            self._layout()
        if parked:
            self.park(self._prompt, self._editor, self._placeholder)
        if self.size != old_size and self.on_resize:
            self.on_resize()

    # ---- 描画 ---------------------------------------------------------

    def _out(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def _ansi(self, text: Text) -> str:
        with self.console.capture() as capture:
            self.console.print(text, end="", soft_wrap=True, highlight=False)
        return capture.get()

    def _input_row(self) -> str:
        """入力欄の 1 行を描く文字列。入力待ちのあいだは、最後にカーソルを打つ位置へ置く。"""
        row = self.rows + 2
        if not self._parked:
            idle = self._idle.copy()
            idle.truncate(self._columns - 1, overflow="ellipsis")
            return f"\x1b[{row};1H{self._ansi(idle)}\x1b[K"
        prompt = self._prompt
        if prompt.cell_len > self._columns - 20:  # 現在地が長すぎて打つ場所がなくなるとき
            prompt = prompt[-2:]
        shown, column = self._editor.view(self._columns - prompt.cell_len)
        # 何も打っていないあいだは、何を打てばよいかを薄い文字で出しておく(1 文字打つと消える)
        hint = Text(self._placeholder, style="d.placeholder") if not self._editor.text else Text()
        hint.truncate(max(0, self._columns - prompt.cell_len - 1), overflow="ellipsis")
        line = self._ansi(Text.assemble(prompt, shown, hint))
        return f"\x1b[{row};1H{line}\x1b[K\x1b[{row};{prompt.cell_len + column + 1}H"

    def _layout(self) -> None:
        """スクロール範囲を表示の部分に絞り、入力欄とステータスバーを描く。"""
        if not self._active:
            return
        top = self.rows + 1
        rule = self._ansi(Text(design.active.RULE * self._columns, style="d.rule"))
        # 入力欄の上の罫線には、右端に場面と操作を入れる
        top_rule = self._ansi(self._top_rule()) if self._title.plain or self._status.plain else rule
        self._out(
            ("" if self._parked else _SAVE)
            + f"\x1b[1;{self.rows}r"
            + f"\x1b[{top};1H{top_rule}\x1b[K"
            + f"\x1b[{top + 2};1H{rule}\x1b[K"
            + "".join(f"\x1b[{top + 3 + n};1H{line}\x1b[K" for n, line in enumerate(self._task_lines))
            + self._input_row()
            + ("" if self._parked else _RESTORE)
        )

    def _top_rule(self) -> Text:
        """入力欄の上の罫線。左端にいまいる場面、右端に使える操作を入れる。"""
        title = self._title.copy()
        title.truncate(max(0, self._columns // 3), overflow="ellipsis")
        rule = design.active.RULE
        left = Text.assemble((f"{rule} ", "d.rule"), title, " ") if title.plain else Text()
        status = self._status.copy()
        status.truncate(max(0, self._columns - left.cell_len - 3), overflow="ellipsis")
        right = Text.assemble(" ", status, (f" {rule}", "d.rule")) if status.plain else Text()
        fill = design.active.RULE * max(0, self._columns - left.cell_len - right.cell_len)
        return Text.assemble(left, (fill, "d.rule"), right)

    def set_status(self, *parts: str, title: str = "") -> None:
        """入力欄の上の罫線に出す内容を差し替える。どちらも Rich のマークアップ。

        title は左端に出す、いまいる場面(開いているミッションなど)。
        parts は右端に出す、使える操作。区切り線でつなぐ。
        """
        self._title = Text.from_markup(title)
        self._status = Text.from_markup(f" [d.rule]{design.active.SEPARATOR}[/] ".join(parts))
        self._layout()

    def set_idle(self, message: str | None) -> None:
        """コマンドの実行中、入力欄に出しておく一言(閉じ方の案内など)を差し替える。None で消す。"""
        idle = Text(f"  {message}", style="d.muted") if message else Text()
        if idle.plain == self._idle.plain:
            return
        self._idle = idle
        if self._active and not self._parked:
            self._out(_SAVE + self._input_row() + _RESTORE)

    def set_task(self, task: Callable[[int], Text] | None) -> None:
        """ステータスバーに出すお題を差し替える。task は幅を受け取り、折り返した文章を返す。None で消す。"""
        self._reflow(lambda: setattr(self, "_task", task))

    def park(self, prompt: Text, editor: LineEditor, placeholder: str = "") -> None:
        """入力欄に prompt と編集中の行を描き、カーソルをそこへ置く。

        placeholder は、何も打っていないあいだ入力欄に薄く出しておく案内。
        """
        first = not self._parked
        self._parked = True
        self._prompt, self._editor, self._placeholder = prompt, editor, placeholder
        # vim などがカーソルの形を変えたまま終わることがあるので、入力欄に来るたびに形を指定し直す
        self._out((_SAVE if first else "") + self._input_row() + (design.active.CURSOR if first else ""))

    def unpark(self) -> None:
        """入力欄を空にして、カーソルを表示の部分へ戻す。"""
        if self._parked:
            self._parked = False
            self._out(self._input_row() + _RESTORE)

    # ---- 表示の部分への出力 -------------------------------------------

    def write(self, data: bytes) -> None:
        """シェルの出力を表示の部分へそのまま書く。"""
        self.unpark()
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        if _DAMAGE.search(data):
            self._layout()

    def clear(self) -> None:
        """表示の部分を空にして、先頭から書き始める。"""
        self.unpark()
        self._out("\x1b[2J\x1b[H")
        self._layout()

    def show(self, text: Text) -> None:
        """表示の部分に 1 つ書き足す。入力中の行はそのまま残る。"""
        self.unpark()
        self.console.print(text, highlight=False)

    def echo(self, prompt: Text, line: str) -> None:
        """確定した入力を、表示の部分に 1 行として残す。背景は行の右端まで付ける。"""
        self.unpark()
        entry = Text.assemble(prompt, (line, "d.echo.text"), style=ECHO_STYLE)
        entry.pad_right(max(0, self._columns - entry.cell_len))
        self.console.print(entry, soft_wrap=True, highlight=False)

    # ---- 入力 ---------------------------------------------------------

    def read_line(
        self,
        prompt: Text = MENU_PROMPT,
        shortcuts: dict[str, str] | None = None,
        placeholder: str = "",
        redraw_on_resize: bool = False,
    ) -> str | None:
        """入力欄から 1 行読む。Ctrl-D や Ctrl-C なら None。

        placeholder は、何も打っていないあいだ入力欄に薄く出しておく案内。
        redraw_on_resize が True なら、端末の大きさが変わったときに RESIZED を返す
        (呼び出し側が、新しい大きさに合わせて画面を描き直すため。打ちかけの文字は捨てる)。

        shortcuts は「キー → 返す文字列」。何も打っていないときにそのキーが押されたら、
        Enter を待たずにその文字列を返す(一覧のページ送りに使う)。
        """
        editor = LineEditor()
        while True:
            self.park(prompt, editor, placeholder)
            readable, _, _ = select.select([self.input_fd, self.wake_fd], [], [])
            if self.wake_fd in readable and self.check_resize() and redraw_on_resize:
                # 表示の部分は、前の大きさに合わせて描いてある。呼び出し側に描き直してもらう
                self.unpark()
                return RESIZED
            if self.input_fd not in readable:
                continue
            typed = os.read(self.input_fd, 1024)
            if not typed:  # 入力が閉じられた
                self.unpark()
                return None
            for key in editor.keys(typed):
                if shortcuts and key in shortcuts and not editor.text:
                    self.unpark()
                    return shortcuts[key]
                action = editor.press(key)
                if action == SUBMIT:
                    line = editor.take()
                    if line.strip():
                        self.echo(prompt, line)
                    self.unpark()
                    return line
                if action in {EOF, INTERRUPT}:
                    self.unpark()
                    return None
