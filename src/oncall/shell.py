"""ミッション用のシェルを擬似端末で動かし、プレイヤーの端末との間で入出力を中継する。

プレイヤーが入力欄で確定した 1 行を、本物の bash に渡して実行する。アプリは中継している出力の中から
bashrc が埋め込む目印(開始・終了)を読み取り、「どの行を打ち、何が出力され、どう終わったか」を
1 行ごとに受け取る。色や複数列の表示、less や man といった対話的なコマンドもそのまま動く。

打鍵の行き先は 2 通りある。シェルが次の行を待っているあいだは入力欄(行の編集はアプリが行う)、
コマンドの実行中はそのコマンド(1 文字ずつそのまま渡す)。
"""

from __future__ import annotations

import base64
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from oncall.complete import complete
from oncall import design
from oncall.catalog import commands_in
from oncall.lineedit import EOF, INTERRUPT, SUBMIT, LineEditor
from oncall.screen import Screen

_MARK_START = b"\x1b]7770;"
# 画面全体を使うコマンドと、その閉じ方。開いているあいだ、入力欄に出しておく
_HOW_TO_CLOSE = {
    "less": "q で閉じる",
    "man": "q で閉じる",
    "top": "q で閉じる",
    "vim": "vim を閉じるには: Esc を押してから :q!(保存して閉じるなら :wq)",
    "vi": "vi を閉じるには: Esc を押してから :q!(保存して閉じるなら :wq)",
    "nano": "Ctrl-X で閉じる",
}
_MARK_END = b"\x07"


@dataclass(frozen=True)
class CommandResult:
    """プレイヤーが打った 1 行の結果。"""

    command: str
    status: int
    cwd: str
    output: str  # 端末に出た内容そのまま(色などの制御文字を含む)


Handler = Callable[[CommandResult], bool | None]


def _default_write(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.flush()


class _MarkerParser:
    """出力の流れを、画面に出す部分と目印とに分ける。目印が読み込みの切れ目をまたいでもよい。"""

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        """("text", 中身) と ("mark", 目印の中身) の並びを返す。"""
        data = self._pending + data
        self._pending = b""
        events: list[tuple[str, bytes]] = []
        while data:
            start = data.find(_MARK_START)
            if start == -1:
                # 末尾が目印の書き出しの途中かもしれないので、その分は次回に回す
                keep = next(
                    (n for n in range(min(len(_MARK_START) - 1, len(data)), 0, -1) if _MARK_START.startswith(data[-n:])),
                    0,
                )
                if len(data) > keep:
                    events.append(("text", data[: len(data) - keep]))
                self._pending = data[len(data) - keep :]
                break
            if start:
                events.append(("text", data[:start]))
            end = data.find(_MARK_END, start)
            if end == -1:
                self._pending = data[start:]
                break
            events.append(("mark", data[start + len(_MARK_START) : end]))
            data = data[end + 1 :]
        return events


class Shell:
    def __init__(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        input_fd: int | None = None,
        write: Callable[[bytes], None] | None = None,
        screen: Screen | None = None,
        label: str = "",
    ):
        # screen がなければ画面には何も描かず、入出力の中継だけを行う(テスト用)
        self._screen = screen
        self._input_fd = sys.stdin.fileno() if input_fd is None else input_fd
        self._write = write or (screen.write if screen else _default_write)
        self._parser = _MarkerParser()
        self._events: list[tuple[str, bytes]] = []
        self._capture: bytearray | None = None
        self._ready = False
        self._editor = LineEditor()
        self._keys: list[str] = []  # 入力欄がまだ受け取っていない打鍵
        self._at_prompt = False  # シェルが次の行を待っている
        self._continuing = False  # 引用符が閉じていないなど、行の続きを待っている
        self._place = ""  # 入力欄の先頭に出す現在地
        self._label = label  # 現在地の前に出す「ユーザー名@ホスト名」
        # 次のプロンプトが出て入力待ちになるたびに呼ばれる(テストで次の行を送るのに使う)
        self.on_idle: Callable[[], None] | None = None

        self._master, slave = pty.openpty()
        self._sync_window_size()
        self._process = subprocess.Popen(
            argv,
            env=env,
            cwd=cwd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            # 擬似端末を制御端末にする(Ctrl-C やジョブ制御が効くようにする)
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
        os.close(slave)

    # ---- 端末の設定 ---------------------------------------------------

    def _sync_window_size(self) -> None:
        """シェルには、画面のうち表示の部分だけを端末の大きさとして見せる。"""
        rows, columns = self._screen.size if self._screen else (24, 80)
        fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    # ---- 入力欄 -------------------------------------------------------

    def _prompt(self) -> Text:
        if self._continuing:
            return Text("> ")
        # 「● user@host:~/dir $」の形。丸は、障害対応中のサーバーにいる印
        parts: list = [(design.active.INCIDENT_MARK, "d.incident"), " "]
        if self._label:
            parts += [(self._label, "d.prompt.host"), ":"]
        parts += [(self._place, "d.prompt.path"), " ", ("$", "d.prompt.mark"), " "]
        return Text.assemble(*parts)

    def _type(self) -> None:
        """溜まっている打鍵を入力欄に反映する。行が確定したらシェルに渡す。"""
        while self._keys and self._at_prompt:
            key = self._keys.pop(0)
            if key == "\t":
                self._complete()
                continue
            action = self._editor.press(key)
            if action == SUBMIT:
                self._submit(self._editor.take())
            elif action == INTERRUPT:
                self._editor.clear()
                if self._continuing:
                    os.write(self._master, b"\x03")
            elif action == EOF:
                os.write(self._master, b"\x04")

    def _complete(self) -> None:
        """Tab キー。カーソルの手前の単語を、ファイル名やコマンド名で補う。"""
        if self._continuing:
            return  # 行の続きを待っているあいだは、シェルに候補を尋ねられない
        editor = self._editor
        start, word, listing = complete(editor.text[: editor.cursor], self.run_hidden)
        editor.replace_word(start, word)
        if listing and self._screen:
            self._screen.show(Text("  ".join(listing), style="d.muted"))

    def _submit(self, line: str) -> None:
        if not line.strip() and not self._continuing:
            return  # 空の Enter では何もしない
        if self._screen:
            self._screen.echo(self._prompt(), line)
            used = commands_in(line)
            self._screen.set_idle(next((how for name, how in _HOW_TO_CLOSE.items() if name in used), None))
        self._at_prompt = False
        # 実行中に打たれてシェルに残っている文字があれば、先に消す(Ctrl-U)
        os.write(self._master, b"\x15" + line.encode() + b"\n")

    # ---- 中継 ---------------------------------------------------------

    def _read_events(self) -> bool:
        """シェルの出力を読み、目印と画面出力に分けて溜める。シェルが終わっていたら False。"""
        try:
            data = os.read(self._master, 65536)
        except OSError:  # シェルが終了すると EIO になる
            return False
        if not data:
            return False
        self._events += self._parser.feed(data)
        return True

    def run(self, handler: Handler, on_ready: Callable[[], None] | None = None) -> bool:
        """シェルが終わるか handler が True を返すまで中継を続ける。

        handler が止めた場合は True、プレイヤーがシェルを抜けた場合は False を返す。
        """
        fds = [self._master, self._input_fd]
        if self._screen:
            self._screen.on_resize = self._sync_window_size
            fds.append(self._screen.wake_fd)
        try:
            while True:
                while self._events:
                    kind, payload = self._events.pop(0)
                    if kind == "text":
                        # 最初のプロンプトより前(シェルの起動時の出力)は見せない
                        if self._ready:
                            self._write(payload)
                            if self._capture is not None:
                                self._capture += payload
                        continue
                    result = self._on_mark(payload)
                    if self._screen and self._at_prompt:
                        self._screen.set_idle(None)
                    if not payload.startswith(b"E;"):
                        continue
                    if self._screen:
                        self._screen.unpark()  # このあとの案内は表示の部分に出る
                    if not self._ready:
                        self._ready = True
                        if on_ready:
                            on_ready()
                    elif result is not None and handler(result):
                        return True
                    if self.on_idle:
                        self.on_idle()

                self._type()
                if self._at_prompt and self._screen:
                    self._screen.park(self._prompt(), self._editor)
                readable, _, _ = select.select(fds, [], [])
                if self._screen and self._screen.wake_fd in readable:
                    self._screen.check_resize()
                if self._master in readable and not self._read_events():
                    return False
                if self._input_fd in readable:
                    typed = os.read(self._input_fd, 1024)
                    if not typed:  # 入力が閉じられた
                        return False
                    if self._at_prompt:
                        self._keys += self._editor.keys(typed)
                    elif self._ready:
                        os.write(self._master, typed)
        finally:
            if self._screen:
                self._screen.unpark()
                self._screen.on_resize = None

    def _on_mark(self, payload: bytes) -> CommandResult | None:
        """終了の目印なら、その行の結果を返す。コマンドを実行せずにプロンプトへ戻ったときは None。"""
        fields = payload.split(b";")
        if fields[0] == b"S":
            self._capture = bytearray()
            self._at_prompt = False
            return None
        if fields[0] == b"C":
            self._at_prompt = self._continuing = True
            return None
        if fields[0] != b"E" or len(fields) != 5:
            return None
        self._at_prompt, self._continuing = True, False
        self._place = base64.b64decode(fields[4]).decode(errors="replace")
        if self._capture is None:
            return None
        output = self._capture.decode(errors="replace")
        self._capture = None
        return CommandResult(
            command=base64.b64decode(fields[3]).decode(errors="replace").strip(),
            status=int(fields[1]),
            cwd=base64.b64decode(fields[2]).decode(errors="replace"),
            output=output,
        )

    def run_hidden(self, command: str) -> str:
        """画面に出さずにシェルの中でコマンドを実行し、その出力を返す。

        お題の前の場面転換や、補完の候補を尋ねるのに使う。シェルが次の行を待っているときに呼ぶ。
        実行が終わるまで待つ。履歴には残さないので、↑ キーでプレイヤーに見えることはない。
        """
        self._events.clear()
        os.write(self._master, f"\x15{command}; history -d -1\n".encode())
        output = bytearray()
        while True:
            while self._events:
                kind, payload = self._events.pop(0)
                if kind == "text":
                    output += payload
                elif payload.startswith(b"E;"):
                    self._on_mark(payload)  # 移動していれば、入力欄に出す現在地が変わる
                    self._capture = None
                    return output.decode(errors="replace")
            if not self._read_events():
                return ""

    def close(self) -> None:
        try:
            self._process.kill()
        except OSError:
            pass
        self._process.wait()
        try:
            os.close(self._master)
        except OSError:
            pass
