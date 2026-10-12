"""Tab キーでの補完。

候補はミッションのシェル自身に尋ねる(bash の compgen)。シェルは隔離された環境の中にいるので、
そこから見えるファイルやコマンドを正しく挙げられるのはシェルだけだからである。
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable

# カーソルの手前にある、補完する単語(\ で打ち消した空白は単語の一部)
_WORD = re.compile(r"(?:\\.|[^\s\\])*$")
_ESCAPED = re.compile(r"\\(.)")
# 単語の中にそのまま書けない文字。補完した名前に含まれていたら \ を付ける
_SPECIAL = re.compile(r"([^\w@%+=:,./~-])")
_SEPARATOR = "//"  # compgen の 2 つの結果の境目。ファイル名にはなりえない


def _quote(word: str) -> str:
    """シェルに渡す形にする。先頭の ~/ はホームとして展開させる。"""
    if word.startswith("~/"):
        return "~/" + shlex.quote(word[2:])
    return shlex.quote(word)


def complete(before: str, ask: Callable[[str], str]) -> tuple[int, str, list[str]]:
    """カーソルの手前の文字列 before の、最後の単語を補完する。

    ask はシェルでコマンドを実行して出力を返す関数。
    (単語の開始位置, 置き換える単語, 一覧に出す候補) を返す。補えるところまで補い、
    それ以上は絞れないときに候補を返す。候補がなければ単語はそのまま。
    """
    start = _WORD.search(before).start()
    typed = before[start:]
    word = _ESCAPED.sub(r"\1", typed)
    head = before[:start].rstrip()
    # 行の先頭や | ; & の直後は、コマンド名を補う。それ以外はファイル名
    if (not head or head[-1] in "|;&") and "/" not in word:
        names = {name for name in ask(f"compgen -c -- {_quote(word)}").split() if not name.startswith("__oncall")}
        directories: set[str] = set()
    else:
        quoted = _quote(word)
        files, _, found = ask(f"compgen -f -- {quoted}; echo {_SEPARATOR}; compgen -d -- {quoted}").partition(_SEPARATOR)
        names = {os.path.basename(line.strip()) for line in files.splitlines() if line.strip()}
        directories = {os.path.basename(line.strip()) for line in found.splitlines() if line.strip()}

    partial = word.rpartition("/")[2]
    if not partial.startswith("."):
        names = {name for name in names if not name.startswith(".")}  # 隠しファイルは . から打ったときだけ
    if not names:
        return start, typed, []

    stem = word[: len(word) - len(partial)]
    if len(names) == 1:
        (name,) = names
        return start, _SPECIAL.sub(r"\\\1", stem + name) + ("/" if name in directories else " "), []
    common = os.path.commonprefix(sorted(names))
    if len(common) > len(partial):
        return start, _SPECIAL.sub(r"\\\1", stem + common), []
    return start, typed, [name + ("/" if name in directories else "") for name in sorted(names)]
