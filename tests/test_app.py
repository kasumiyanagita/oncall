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


def test_exit_works_only_on_the_top_page(tmp_path):
    """ミッション・見学・図鑑・チュートリアルで exit と打っても終了しない。title で戻ってから exit で終了する。"""
    steps = ["\r", "exit\r", "ls\r", "title\r"]  # Enter だけで、未解決の最初のミッションが始まる
    steps += ["senpai\r", "exit\r", "1\r", "exit\r", "title\r"]
    steps += ["zukan\r", "exit\r", "title\r", "tutorial\r", "exit\r", "title\r", "exit\r"]
    screen, code = run_app(tmp_path, steps)
    assert "Traceback" not in screen
    assert "access.log" in screen  # exit のあとも、ミッションのシェルは動いている
    assert screen.count("exit はトップページで使えます") == 2  # ミッションと見学で 1 回ずつ
    assert screen.count("インシデント一覧") == 5 and screen.count("お疲れさまでした") == 1 and code == 0


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


def test_watch_the_senior_work_to_the_end(tmp_path):
    """見学: Enter を押すたびに、先輩がコマンドを打ち、解説が出る。最後まで見ると一覧に戻り、見た印が付く。"""
    from oncall.tour import load_tours

    tour = load_tours(load_missions())[0]
    steps = ["senpai\r", "\r"] + ["\r", "\r"] * len(tour.steps) + ["\r", "title\r", "exit\r"]
    screen, code = run_app(tmp_path, steps)
    assert "Traceback" not in screen
    assert "見学: 先輩の作業を見る" in screen and f"見学「数える」 {len(tour.steps)}/{len(tour.steps)}" in screen
    assert "cut -d@ -f2 | sort | uniq -c | sort -rn | head -5" in screen
    assert "status=bounced" in screen  # 先輩のコマンドが、本当に実行されている
    assert "「数える」のやり方は、インシデント「深夜の 500 祭り」「消えた注文」で使えます" in screen
    assert '"count"' in (tmp_path / "progress.json").read_text().split('"watched"')[1]
    assert screen.count("インシデント一覧") == 2 and code == 0
