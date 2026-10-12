from rich.cells import cell_len

from oncall.prose import CODE_STYLE, STRONG_STYLE, render_prose


def _lines(source, width):
    return render_prose(source, width).plain.split("\n")


def test_lines_fit_width_and_fill_it():
    source = "このログには、レスポンスサイズがちょうど 500 バイトの正常なリクエストや、URL に `/500` を含むリクエストが大量に混ざっていました。"
    lines = _lines(source, 40)
    assert all(cell_len(line) <= 40 for line in lines)
    # 和文の途中でも折り返すので、最終行以外はほぼ幅いっぱいまで埋まる
    assert all(cell_len(line) >= 30 for line in lines[:-1])


def test_ascii_words_are_not_split():
    lines = _lines("あいうえおかきくけこ `sort -rn` さしすせそ", 14)
    assert not any(line.endswith("so") or line.startswith("rt") for line in lines)
    assert "".join(lines).replace(" ", "") == "あいうえおかきくけこsort-rnさしすせそ"


def test_punctuation_never_starts_a_line():
    lines = _lines("あいうえお、かきくけこ。さしすせそ、たちつてと。", 10)
    assert not any(line[0] in "、。" for line in lines)


def test_numbered_list_has_hanging_indent():
    lines = _lines("1. あいうえおかきくけこさしすせそ\n2. たちつてと", 16)
    assert lines[0].startswith(" 1. ")
    assert lines[1].startswith("    ") and not lines[1].startswith(" 2.")
    assert lines[-1] == " 2. たちつてと"


def test_code_and_paragraphs():
    text = render_prose("最初に `head -3 access.log` を打つ。\n\n次の段落。", 60)
    assert text.plain == "最初に head -3 access.log を打つ。\n\n次の段落。"
    assert any(str(span.style) == CODE_STYLE for span in text.spans)


def test_code_inside_strong_is_rendered_without_backticks():
    text = render_prose("**`sed -i` は元のファイルを書き換えます。**確認が先です。", 80)
    assert text.plain == "sed -i は元のファイルを書き換えます。確認が先です。"
    style_at = {i: str(span.style) for span in text.spans for i in range(span.start, span.end)}
    assert {style_at[i] for i in range(len("sed -i"))} == {CODE_STYLE}
    assert {style_at[i] for i in range(len("sed -i"), text.plain.index("確認"))} == {STRONG_STYLE}
    assert text.plain.index("確認") not in style_at
