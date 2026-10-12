"""入力欄の行編集: 打鍵の解釈、履歴、長い行の表示、Tab での補完。"""

import subprocess

from oncall.complete import complete
from oncall.lineedit import EOF, INTERRUPT, SUBMIT, LineEditor


def type_keys(editor, data):
    return [editor.press(key) for key in editor.keys(data.encode())]


def test_typing_and_moving_the_cursor():
    editor = LineEditor()
    type_keys(editor, "ls -l\x1b[D\x1b[Da")
    assert (editor.text, editor.cursor) == ("ls a-l", 4)
    type_keys(editor, "\x7f\x01x\x05y")
    assert editor.text == "xls -ly"


def test_enter_submits_and_take_empties_the_line():
    editor = LineEditor()
    assert type_keys(editor, "pwd\r") == [None, None, None, SUBMIT]
    assert editor.take() == "pwd"
    assert editor.text == ""


def test_ctrl_c_and_ctrl_d():
    editor = LineEditor()
    assert type_keys(editor, "\x04") == [EOF]
    assert type_keys(editor, "ab\x01\x04") == [None, None, None, None]  # 行が空でなければ 1 文字消す
    assert editor.text == "b"
    assert type_keys(editor, "\x03") == [INTERRUPT]


def test_kill_keys():
    editor = LineEditor()
    type_keys(editor, "cat memo.txt  \x17")
    assert editor.text == "cat "
    type_keys(editor, "a.txt\x1b[D\x1b[D\x0b")
    assert editor.text == "cat a.t"
    type_keys(editor, "\x15")
    assert editor.text == ""


def test_history_goes_back_and_returns_to_the_draft():
    editor = LineEditor()
    for line in ("ls", "pwd"):
        type_keys(editor, line)
        editor.take()
    type_keys(editor, "ca\x1b[A")
    assert editor.text == "pwd"
    type_keys(editor, "\x1b[A\x1b[A")
    assert editor.text == "ls"
    type_keys(editor, "\x1b[B\x1b[B")
    assert editor.text == "ca"


def test_japanese_split_across_reads_and_unknown_sequences():
    editor = LineEditor()
    data = "echo 日本".encode()
    for key in editor.keys(data[:-2]) + editor.keys(data[-2:]):
        editor.press(key)
    assert editor.text == "echo 日本"
    type_keys(editor, "\t\x1b[200~\x1b[15~\x1b")  # Tab や未対応のキーは無視する
    assert editor.text == "echo 日本"


def test_view_scrolls_a_long_line_to_keep_the_cursor_visible():
    editor = LineEditor()
    type_keys(editor, "abcdefghij")
    assert editor.view(20) == ("abcdefghij", 10)
    assert editor.view(5) == ("ghij", 4)
    type_keys(editor, "\x01")
    assert editor.view(5) == ("abcde", 0)
    wide = LineEditor()
    type_keys(wide, "日本語")
    assert wide.view(5) == ("本語", 4)  # 全角は 2 桁と数える


def ask_in(directory):
    """補完の候補を、directory にいる本物の bash に尋ねる。"""

    def ask(command):
        return subprocess.run(["bash", "-c", command], cwd=directory, capture_output=True, text=True).stdout

    return ask


def test_tab_completes_file_names(tmp_path):
    for name in ("memo.txt", "menu.txt", "todo.txt", ".settings", "my file.txt"):
        (tmp_path / name).touch()
    (tmp_path / "documents" / "reports").mkdir(parents=True)
    ask = ask_in(tmp_path)
    assert complete("cat to", ask) == (4, "todo.txt ", [])
    assert complete("cd doc", ask) == (3, "documents/", [])
    assert complete("ls documents/", ask) == (3, "documents/reports/", [])
    assert complete("cat my", ask) == (4, "my\\ file.txt ", [])
    # 候補が絞れないときは、共通する部分まで補い、それ以上は一覧を返す
    assert complete("cat m", ask) == (4, "m", ["memo.txt", "menu.txt", "my file.txt"])
    assert complete("cat me", ask) == (4, "me", ["memo.txt", "menu.txt"])
    assert complete("cat mem", ask) == (4, "memo.txt ", [])
    assert complete("cat nothing", ask) == (4, "nothing", [])
    # 隠しファイルは . から打ったときだけ
    assert ".settings" not in complete("cat ", ask)[2]
    assert complete("cat .s", ask) == (4, ".settings ", [])


def test_tab_completes_command_names_at_the_start(tmp_path):
    ask = ask_in(tmp_path)
    assert complete("mkdi", ask) == (0, "mkdir ", [])
    assert complete("ls | tai", ask) == (5, "tail ", [])
    assert "pwd" in complete("pw", ask)[2]
