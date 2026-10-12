"""ゲーム全体を擬似端末で起動し、トップページからミッションを解いて終了するまでを通す。"""

import fcntl
import os
import pty
import re
import select
import struct
import sys
import termios
import time

import pytest

from oncall.mission import load_missions

SAVED = '{"version": 1, "tutorial_seen": true, "missions": {}, "unlocked": {}, "usage": {}}'


def run_app(tmp_path, steps, rows=30, columns=100, first_time=False):
    """oncall を起動し、steps のキー入力を順に送る。(画面に出た内容, 終了コード) を返す。

    first_time でなければ、チュートリアルを見終えた状態(トップページから始まる)にしておく。
    """
    save = tmp_path / "progress.json"
    if not first_time and not save.exists():
        save.write_text(SAVED)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(ONCALL_DATA_DIR=str(tmp_path), TERM="xterm-256color")
        os.execv(sys.executable, [sys.executable, "-m", "oncall", "--seed", "1"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    output = bytearray()

    def read_until_quiet(quiet=0.8, limit=20.0):
        """出力が quiet 秒とぎれるまで読む(マシンが重くても、ゲームの準備が終わるのを待てる)。"""
        deadline = time.time() + limit
        last = time.time()
        while time.time() < deadline and time.time() - last < quiet:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                output.extend(data)
                last = time.time()

    read_until_quiet(quiet=1.5)
    for keys in steps:
        if isinstance(keys, tuple):  # (行数, 桁数): 端末の大きさを変える
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", *keys, 0, 0))
        else:
            os.write(fd, keys.encode())
        read_until_quiet()
    # 最後の入力で終了するはずなので、終わるまで少し待つ
    deadline = time.time() + 10
    finished, status = os.waitpid(pid, os.WNOHANG)
    while not finished and time.time() < deadline:
        time.sleep(0.1)
        finished, status = os.waitpid(pid, os.WNOHANG)
    if not finished:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        pytest.fail("ゲームが終了しなかった:\n" + output.decode(errors="replace")[-2000:])
    # 色などの制御文字列を取り除いて、出た文字だけを比べられるようにする
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[78]", "", output.decode(errors="replace"))
    return text, os.waitstatus_to_exitcode(status)


def test_solve_a_mission_and_go_back_to_the_top_page(tmp_path):
    solution = load_missions()[0].solution
    screen, code = run_app(tmp_path, ["1\r", "check\r", "hint\r", solution + "\r", "check\r", "title\r", "exit\r"])
    assert "Traceback" not in screen and "内部エラー" not in screen
    assert "まだ解決していません" in screen and "ヒント 1/3" in screen
    assert "INCIDENT RESOLVED" in screen and "図鑑に 5 個のコマンドを登録しました" in screen
    assert screen.count("インシデント一覧") == 2  # トップページが 2 回出ている(起動時と、title で戻ったとき)
    assert "お疲れさまでした" in screen and code == 0
    assert '"error-ips"' in (tmp_path / "progress.json").read_text()


def test_exit_in_a_mission_quits_the_game(tmp_path):
    screen, code = run_app(tmp_path, ["\r", "ls\r", "exit\r"])  # Enter だけで、未解決の最初のミッションが始まる
    assert "Traceback" not in screen
    assert "access.log" in screen
    assert screen.count("インシデント一覧") == 1 and "お疲れさまでした" in screen and code == 0


def test_only_exit_quits_from_the_top_page(tmp_path):
    screen, code = run_app(tmp_path, ["zzz\r", "99\r", "q\r", "title\r", "exit\r"])
    assert "Traceback" not in screen
    assert screen.count("番号を入力してください") == 4
    assert "お疲れさまでした" in screen and code == 0


def test_zukan_and_tutorial_from_the_top_page(tmp_path):
    screen, code = run_app(tmp_path, ["zukan\r", "grep\r", "\r", "tutorial\r", "\x1b[C", "title\r", "exit\r"])
    assert "Traceback" not in screen
    assert "コマンド図鑑" in screen and "まだ図鑑に登録されていません" in screen
    assert "チュートリアル 1/4" in screen and "チュートリアル 2/4" in screen
    assert screen.count("インシデント一覧") == 3 and code == 0


def test_tutorial_shows_on_first_launch_only(tmp_path):
    screen, code = run_app(tmp_path, ["\r", "\r", "\r", "\r", "exit\r"], first_time=True)
    assert "チュートリアル 1/4" in screen and "チュートリアル 4/4" in screen
    assert screen.index("チュートリアル 4/4") < screen.index("インシデント一覧") and code == 0
    screen, code = run_app(tmp_path, ["exit\r"])
    assert "チュートリアル 1/4" not in screen and "インシデント一覧" in screen and code == 0


def test_plain_oncall_never_opens_a_retro_tab(monkeypatch):
    """oncall だけなら通常のデザインで、いまの端末で動く。タブを開くのは --retro のときだけ。"""
    from oncall import cli, retro

    opened = []
    monkeypatch.setattr(retro, "can_open_tab", lambda: True)
    monkeypatch.setattr(retro, "open_tab", lambda arguments: opened.append(arguments) or True)
    monkeypatch.delenv("ONCALL_RETRO", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # 端末がないので、タブを開かなければここで終わる
    assert cli.main([]) == 2 and opened == []
    assert cli.main(["--retro"]) == 0 and opened == [["--retro"]]
    monkeypatch.setenv("ONCALL_RETRO", "1")
    assert cli.main(["--retro"]) == 2 and len(opened) == 1


def test_top_page_is_redrawn_when_the_terminal_is_resized(tmp_path):
    screen, code = run_app(tmp_path, [(24, 70), (40, 120), "exit\r"])
    assert "Traceback" not in screen
    assert screen.count("インシデント一覧") == 3  # 大きさを変えるたびに、トップページを描き直す
    assert "お疲れさまでした" in screen and code == 0
