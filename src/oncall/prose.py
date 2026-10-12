"""ヒントや解説の文章を端末幅に合わせて整形する。

Rich の Markdown は空白でしか改行しないため、空白のない日本語の途中に `コマンド` が
挟まると行がばらばらに折れる。ここでは和文の文字間でも改行できる折り返しを自前で行う。

対応する書式は、段落(空行区切り)、番号付きリスト(`1. `)、`コード`、**強調** だけ。
"""

from __future__ import annotations

import re

from rich.cells import cell_len
from rich.text import Text

# 文章が横に伸びすぎると読みにくいので、広い端末でもこの幅までにする
MAX_WIDTH = 100
PANEL_CHROME = 4  # パネルの枠線と左右の余白

# 文章の中の `コード` と **強調** に付ける、デザイン上の名前(色は designs/ の各ファイルが決める)
CODE_STYLE = "d.command"
STRONG_STYLE = "d.strong"

_INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*")
# 空白 / ASCII の単語 / それ以外の 1 文字(和文は 1 文字ずつ改行できる)
_TOKEN = re.compile(r"[ \t]+|[\x21-\x7e]+|[^\x20-\x7e\t]")
_LIST_ITEM = re.compile(r"(\d+)\.\s+(.*)")
# 行頭に置かない文字(直前のトークンにくっつける)
_NO_LINE_START = set("、。,.)]}」』】)!?:;:;!?ー")

Segment = tuple[str, str]  # (文字列, スタイル)


def _inline(source: str) -> list[Segment]:
    segments: list[Segment] = []
    pos = 0
    for match in _INLINE.finditer(source):
        if match.start() > pos:
            segments.append((source[pos : match.start()], ""))
        if match.group(1) is not None:
            segments.append((match.group(1), CODE_STYLE))
        else:
            # 強調の中の `コード` も書式として扱う
            segments += [(text, style or STRONG_STYLE) for text, style in _inline(match.group(2))]
        pos = match.end()
    if pos < len(source):
        segments.append((source[pos:], ""))
    return segments


def _tokens(segments: list[Segment]) -> list[list[Segment]]:
    """改行してよい位置で区切ったトークン列。1 トークンは複数スタイルの断片からなる。"""
    tokens: list[list[Segment]] = []
    for text, style in segments:
        for piece in _TOKEN.findall(text):
            if tokens and piece[0] in _NO_LINE_START and not tokens[-1][0][0].isspace():
                tokens[-1].append((piece, style))
            else:
                tokens.append([(piece, style)])
    return tokens


def _wrap(segments: list[Segment], width: int, first_prefix: str, rest_prefix: str) -> list[Text]:
    lines: list[Text] = []
    line = Text(first_prefix)
    used = cell_len(first_prefix)
    empty = True

    for token in _tokens(segments):
        is_space = token[0][0].isspace()
        token_width = sum(cell_len(piece) for piece, _ in token)
        if not empty and used + token_width > width:
            line.rstrip()
            lines.append(line)
            line = Text(rest_prefix)
            used = cell_len(rest_prefix)
            empty = True
        if empty and is_space:
            continue
        for piece, style in token:
            line.append(piece, style=style or None)
        used += token_width
        empty = False

    line.rstrip()
    lines.append(line)
    return lines


def _join_lines(lines: list[str]) -> str:
    """ソース上の改行をつなぐ。英数字どうしの境目にだけ空白を入れる。"""
    joined = ""
    for line in (s.strip() for s in lines):
        if joined and line and joined[-1].isascii() and line[0].isascii():
            joined += " "
        joined += line
    return joined


def render_prose(source: str, width: int, indent: str = "") -> Text:
    """source を幅 width(indent を含む)に収まるよう折り返した Text を返す。"""
    blocks: list[Text] = []
    for block in re.split(r"\n\s*\n", source.strip()):
        raw_lines = block.splitlines()
        if all(_LIST_ITEM.match(line.strip()) for line in raw_lines):
            lines: list[Text] = []
            for raw in raw_lines:
                number, body = _LIST_ITEM.match(raw.strip()).groups()
                prefix = f"{indent} {number}. "
                lines += _wrap(_inline(body), width, prefix, " " * len(prefix))
        else:
            lines = _wrap(_inline(_join_lines(raw_lines)), width, indent, indent)
        blocks.append(Text("\n").join(lines))
    return Text("\n\n").join(blocks)
