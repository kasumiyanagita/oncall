"""入力欄の 1 行編集。打鍵を受け取り、編集中の文字列とカーソルの位置を保つ。

画面には何も描かない。描くのは screen.py の役目で、ここは打鍵の解釈だけを受け持つ。
"""

from __future__ import annotations

import codecs
import re

from rich.cells import cell_len

SUBMIT = "submit"  # Enter で行を確定した
EOF = "eof"  # 空の行で Ctrl-D を押した
INTERRUPT = "interrupt"  # Ctrl-C を押した

# 矢印キーなどのエスケープシーケンス(読み込みの切れ目で途切れたものを含む)/ それ以外の 1 文字
_KEY = re.compile(r"\x1b\[[0-9;?]*(?:[@-~]|$)|\x1bO.|\x1b.?|.", re.DOTALL)

_LEFT = {"\x1b[D", "\x02"}
_RIGHT = {"\x1b[C", "\x06"}
_HOME = {"\x1b[H", "\x1b[1~", "\x1bOH", "\x01"}
_END = {"\x1b[F", "\x1b[4~", "\x1bOF", "\x05"}
_PREVIOUS = {"\x1b[A", "\x10"}
_NEXT = {"\x1b[B", "\x0e"}


class LineEditor:
    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0
        self._history: list[str] = []
        self._recall = 0  # ↑↓ で見ている履歴の位置。len(_history) なら編集中の行
        self._draft = ""  # 履歴をさかのぼる前に打ちかけていた行
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def keys(self, data: bytes) -> list[str]:
        """端末から読んだバイト列を、打鍵 1 つずつに分ける。"""
        return _KEY.findall(self._decoder.decode(data))

    def press(self, key: str) -> str | None:
        """打鍵 1 つを反映する。行の確定などがあれば SUBMIT / EOF / INTERRUPT を返す。"""
        if key in {"\r", "\n"}:
            return SUBMIT
        if key == "\x03":
            return INTERRUPT
        if key == "\x04" and not self.text:
            return EOF

        before, after = self.text[: self.cursor], self.text[self.cursor :]
        if key in {"\x7f", "\x08"}:
            self._set(before[:-1], after)
        elif key in {"\x1b[3~", "\x04"}:
            self._set(before, after[1:])
        elif key == "\x15":
            self._set("", after)
        elif key == "\x0b":
            self._set(before, "")
        elif key == "\x17":
            kept = before.rstrip()
            self._set(kept[: kept.rfind(" ") + 1], after)
        elif key in _LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key in _RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key in _HOME:
            self.cursor = 0
        elif key in _END:
            self.cursor = len(self.text)
        elif key in _PREVIOUS:
            self._recall_to(self._recall - 1)
        elif key in _NEXT:
            self._recall_to(self._recall + 1)
        elif len(key) == 1 and key.isprintable():
            self._set(before + key, after)
        return None

    def _set(self, before: str, after: str) -> None:
        self.text = before + after
        self.cursor = len(before)

    def _recall_to(self, index: int) -> None:
        if not 0 <= index <= len(self._history):
            return
        if self._recall == len(self._history):
            self._draft = self.text
        self._recall = index
        self._set(self._draft if index == len(self._history) else self._history[index], "")

    def replace_word(self, start: int, word: str) -> None:
        """start からカーソルの手前までを word に置き換える(補完に使う)。"""
        self._set(self.text[:start] + word, self.text[self.cursor :])

    def clear(self) -> None:
        self._set("", "")
        self._recall = len(self._history)

    def take(self) -> str:
        """確定した行を取り出して、入力欄を空にする。行は履歴に残す。"""
        line = self.text
        if line.strip():
            self._history.append(line)
        self.clear()
        return line

    def view(self, width: int) -> tuple[str, int]:
        """幅 width 桁に収まる表示部分と、その中でのカーソルの桁を返す。

        収まらない長い行は、カーソルが見えるように左へずらす。
        """
        width = max(width, 1)
        start = 0
        while cell_len(self.text[start : self.cursor]) >= width:
            start += 1
        shown = ""
        for char in self.text[start:]:
            if cell_len(shown + char) > width:
                break
            shown += char
        return shown, cell_len(self.text[start : self.cursor])
