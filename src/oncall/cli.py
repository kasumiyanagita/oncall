"""エントリポイントとトップページ。"""

from __future__ import annotations

import argparse
import signal
import sys

from rich.align import Align
from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from oncall import __version__, design, retro, sandbox, tour, tutorial
from oncall.catalog import load_catalog
from oncall.mission import Mission, load_missions
from oncall.progress import Progress, default_path
from oncall.screen import RESIZED, Screen
from oncall.session import MissionSession, format_duration, rank_markup
from oncall.zukan import Zukan

MODE_LABELS = {
    sandbox.ISOLATED: "[d.mode.isolated]隔離モード[/]で実行中",
    sandbox.GUARD: "[d.mode.guard]簡易モード[/]で実行中",
}

ZUKAN = "zukan"
TUTORIAL = "tutorial"
SENPAI = "senpai"
NEXT_PAGE = "next-page"
PREVIOUS_PAGE = "previous-page"
REDRAW = "redraw"  # 端末の大きさが変わったので、トップページを新しい大きさで描き直す

FRAME_ROWS = 2  # 一覧を囲む枠の、上下の線
FRAME_WIDTH = 100  # 一覧を囲む枠の幅の上限
MIN_MENU_ROWS = 3  # 端末が小さくても、1 ページにこの件数は並べる
MENU_SPARE_ROWS = 2
GAUGE_WIDTH = 20  # 一覧の下に出す進捗バーの長さ(桁数)
MENU_PLACEHOLDER = "Enter / 番号を入力(例: 1)"
# 一覧で何も打っていないときの矢印キー。→ で次、← で前のページへ移る
PAGE_KEYS = {"\x1b[C": NEXT_PAGE, "\x1bOC": NEXT_PAGE, "\x1b[D": PREVIOUS_PAGE, "\x1bOD": PREVIOUS_PAGE}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="oncall", description="障害対応をこなしながら Linux コマンドを身につけるターミナルゲーム"
    )
    parser.add_argument("--version", action="version", version=f"oncall {__version__}")
    parser.add_argument(
        "--mode",
        choices=[sandbox.AUTO, sandbox.ISOLATED, sandbox.GUARD],
        default=sandbox.AUTO,
        help="シェルの実行方式 (既定: auto = 使えれば隔離モード)",
    )
    parser.add_argument("--seed", type=int, help="ランダム生成の種を固定する (動作確認用)")
    parser.add_argument("--reset-progress", action="store_true", help="セーブデータを消して最初から始める")
    parser.add_argument("--retro", action="store_true", help="レトロなデザインで動かす (専用のタブを開く)")
    parser.add_argument(
        "--design",
        choices=sorted(design.DESIGNS),
        help="タブを開かず、いまの端末のまま、指定したデザインで描く (動作確認用)",
    )
    parser.add_argument(
        "--retro-install", action="store_true", help="Windows Terminal に、レトロな見た目のプロファイルを追加する"
    )
    parser.add_argument("--retro-remove", action="store_true", help="追加したレトロな見た目のプロファイルを取り除く")
    return parser.parse_args(argv)


def _line_count(console: Console, lines: list[str]) -> int:
    """lines を表示したときに使う行数(幅が狭くて折り返す分を含む)。"""
    with console.capture() as capture:
        for line in lines:
            console.print(line, highlight=False)
    return capture.get().count("\n")


def _result(progress: Progress, mission: Mission) -> str:
    score = progress.best(mission.id)
    if score is None:
        return "[d.muted]未解決[/]"
    return f"{rank_markup(score.rank)} {score.points}pt {format_duration(score.elapsed)}"


def _show_menu(
    console: Console, rows: int, missions: list[Mission], progress: Progress, zukan: Zukan, mode: str, page: int
) -> tuple[int, int]:
    """インシデントの一覧を、rows 行に収まる分だけ表示する。(表示したページ, ページ数) を返す。"""
    look = design.active

    # ロゴと、その下の行(ひとことの説明、実行方式)。下の行を出すかどうかはデザインが決める
    def header_lines(roomy: bool) -> list[str]:
        lines = [f"[d.logo]{look.LOGO.strip(chr(10))}[/]"]
        below = ([f"  {look.TAGLINE}"] if look.TAGLINE else []) + ([f"  {MODE_LABELS[mode]}"] if look.SHOW_MODE else [])
        if not below:
            return [lines[0] + ("\n" if roomy else "")]
        lines[0] += "\n" if roomy else ""
        below[-1] += "\n" if roomy else ""
        return lines + below

    # 進み具合の 2 行は、見出しの幅をそろえて同じ形で出し、見出しに進捗度(%)を添える
    labels = ["解決したインシデント", "コマンド図鑑"]
    label_width = max(cell_len(label) for label in labels)

    def gauge(label: str, done: int, total: int) -> str:
        pad = " " * (label_width - cell_len(label))
        # バーは件数ではなく割合で塗る。1 つでも進んでいれば 1 桁は塗り、全部終えるまでは満杯にしない
        filled = done * GAUGE_WIDTH // total
        filled = min(max(filled, 1 if done else 0), GAUGE_WIDTH - (done < total))
        bar = f"[d.done]{look.GAUGE_ON * filled}[/][d.muted]{look.GAUGE_OFF * (GAUGE_WIDTH - filled)}[/]"
        return f"  [d.muted]{label}{pad} {done * 100 // total:>3}%[/]  {bar}  [d.muted]{done}/{total}[/]"

    solved = sum(1 for m in missions if progress.best(m.id))
    gauges = [gauge(labels[0], solved, len(missions)), gauge(labels[1], zukan.unlocked_count, len(zukan.catalog))]
    legend = f"  [d.done]{look.CHECK}[/] [d.muted]解決済み[/]"

    def footer_lines(roomy: bool) -> list[str]:
        return [*gauges, ("\n" if roomy else "") + legend]

    # 一覧に使える行数は、表示の行数から、ロゴと説明、枠の上下の線、表の見出し、ページの案内(空行 1 行と 2 行)、
    # 下の集計、カーソルのいる 1 行を引いた残り。行数に余裕があれば空行を入れ、打ち間違えたときの一言の分も空けておく
    def room(roomy: bool) -> int:
        used = _line_count(console, header_lines(roomy)) + _line_count(console, footer_lines(roomy))
        return rows - used - FRAME_ROWS - 1 - 1 - (MENU_SPARE_ROWS + 1 if roomy else 0)

    roomy = room(True) >= len(missions)
    per_page = len(missions) if roomy else max(MIN_MENU_ROWS, room(False))
    if per_page < len(missions):
        per_page = max(MIN_MENU_ROWS, per_page - 3)  # ページの案内の分
    starts = list(range(0, len(missions), per_page))
    page %= len(starts)
    shown = missions[starts[page] : starts[page] + per_page]

    table = Table(box=None, padding=(0, 1), header_style="d.muted", expand=True, pad_edge=False)
    table.add_column("No", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("インシデント", no_wrap=True, overflow="ellipsis", ratio=3)
    table.add_column("難度", no_wrap=True)
    table.add_column("主なコマンド", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("結果", no_wrap=True)
    for mission in shown:
        number = missions.index(mission) + 1
        table.add_row(
            f"[d.muted]{number}[/]",
            f"[d.done]{look.CHECK}[/]" if progress.best(mission.id) else " ",
            mission.title,
            f"[d.muted]{look.LEVEL_ON * mission.level}{look.LEVEL_OFF * (5 - mission.level)}[/]",
            f"[d.command]{' '.join(c.name for c in mission.commands)}[/]",
            _result(progress, mission),
        )
    for _ in range(per_page - len(shown)):  # どのページも同じ高さにする
        table.add_row("", "", "", "", "", "")
    body: list = [table]
    if len(starts) > 1:
        body += [
            Text(),
            Align.center(Text.from_markup(f"[d.title]ページ {page + 1}/{len(starts)}[/]")),
            Align.center(Text.from_markup("[d.muted]← → で移動[/]")),
        ]

    for line in header_lines(roomy):
        console.print(line, highlight=False)
    console.print(
        Panel(
            Group(*body),
            title="[d.frame.title]インシデント一覧[/]",
            title_align="left",
            box=look.BOX,
            border_style="d.frame",
            padding=(0, 1),
            width=min(console.width, FRAME_WIDTH),
        )
    )
    for line in footer_lines(roomy):
        console.print(line, highlight=False)
    return page, len(starts)


def _choose(
    screen: Screen, console: Console, missions: list[Mission], progress: Progress, paged: bool
) -> Mission | str | None:
    """選ばれたミッションを返す。図鑑・チュートリアル・ページ送りならその名前、終了なら None。"""
    unsolved = next((m for m in missions if progress.best(m.id) is None), None)
    choices = [f"[d.key]Enter[/] インシデント {missions.index(unsolved) + 1}"] if unsolved else []
    if paged:
        choices.append("[d.key]← →[/] ページ")
    while True:
        screen.set_status(
            *choices,
            f"[d.key]{tour.WORD}[/] 見学",
            "[d.key]zukan[/] 図鑑",
            f"[d.key]{tutorial.WORD}[/] 使い方",
            "[d.key]exit[/] 終了",
        )
        answer = screen.read_line(
            shortcuts=PAGE_KEYS if paged else None, placeholder=MENU_PLACEHOLDER, redraw_on_resize=True
        )
        if answer == RESIZED:
            return REDRAW
        if answer is None:
            return None
        answer = answer.strip().lower()
        if answer == "exit":
            return None
        if answer == ZUKAN:
            return ZUKAN
        if answer == tutorial.WORD:
            return TUTORIAL
        if answer == tour.WORD:
            return SENPAI
        if paged and answer in {NEXT_PAGE, PREVIOUS_PAGE}:
            return answer
        if not answer and unsolved:
            return unsolved
        if answer.isdigit() and 1 <= int(answer) <= len(missions):
            return missions[int(answer) - 1]
        console.print(f"[d.error]1〜{len(missions)} の番号を入力してください。[/]")


def _reset_progress(console: Console) -> bool:
    """確認のうえセーブデータを消す。取りやめたら False。"""
    path = default_path()
    if not path.exists():
        console.print("セーブデータはまだありません。")
        return True
    answer = console.input(f"セーブデータ {path} を消します。よろしいですか? ([d.key]y[/]/N) > ")
    if answer.strip().lower() != "y":
        console.print("取りやめました。")
        return False
    path.unlink()
    console.print("セーブデータを消しました。\n")
    return True


def _confirm_guard(console: Console) -> bool:
    """隔離モードを使えなかったことを知らせ、簡易モードで始めてよいかを尋ねる。"""
    console.print(
        "[d.warn]隔離モードで実行できなかったので、簡易モードで実行します。[/]\n"
        "[d.muted]簡易モードは、作業用の場所の外への変更を止めますが、完全な隔離ではありません。[/]"
    )
    answer = console.input("よろしいですか? ([d.key]y[/]/n) > ")
    if answer.strip().lower() != "y":
        console.print("取りやめました。")
        return False
    return True


def _exit_on_signal(signum: int, _frame) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    design.use(args.design or ("retro" if args.retro else design.default_name()))
    console = Console(theme=design.theme())

    if args.retro_install or args.retro_remove:
        console.print(retro.update_profile(install=args.retro_install), highlight=False)
        return 0
    # --retro: レトロなタブを開いて、そちらで動かす(フォントや色味は端末の側で決まるため)。
    # すでにそのタブの中にいるときは、そのままレトロのデザインで描く
    if args.retro and not retro.inside_tab():
        arguments = [a for a in (sys.argv[1:] if argv is None else argv)]
        if retro.can_open_tab() and retro.open_tab(arguments):
            console.print("レトロなデザインのタブで oncall を開きました。")
            return 0
        console.print(
            "[d.warn]レトロなタブを開けなかったので、いまの端末のまま、レトロのデザインで動かします。[/]\n"
            "[d.muted]フォントや背景の色まで変えるには、先に oncall --retro-install を実行してください。[/]"
        )

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("[d.error]oncall は対話的なターミナルで起動してください。[/]")
        return 2
    if args.mode == sandbox.ISOLATED and not sandbox.isolation_available():
        console.print(
            "[d.error]この環境では隔離モードを使えません(unshare による名前空間の作成に失敗)。[/]\n"
            "--mode guard で簡易モードを使うか、--mode を外して自動選択にしてください。"
        )
        return 2

    # 端末を閉じられたときも、使い捨てディレクトリの後始末(finally)を走らせる
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, _exit_on_signal)

    if args.reset_progress and not _reset_progress(console):
        return 0

    mode = sandbox.resolve_mode(args.mode)
    # 隔離できない環境で自動的に簡易モードになるときは、守りが弱くなるので、始める前に確かめる
    if args.mode == sandbox.AUTO and mode == sandbox.GUARD and not _confirm_guard(console):
        return 0
    missions = load_missions()
    progress = Progress.load()
    if progress.quarantined:
        console.print(
            "[d.warn]セーブデータを読み込めなかったため、新しく始めます。"
            f"元のファイルは {progress.quarantined} に残してあります。[/]"
        )
    zukan = Zukan(load_catalog(), missions, progress)
    tours = tour.load_tours(missions)

    def goodbye() -> int:
        console.print("お疲れさまでした。")
        return 0

    # ここから先は、画面を「表示・入力欄・ステータスバー」に分けて使う
    with Screen(console) as screen:
        # 初めて起動したときだけ、最初にチュートリアルを見せる。途中でやめても、見せたものとして記録する
        if not progress.tutorial_seen:
            progress.mark_tutorial_seen()
            tutorial.run(screen, console, mode)
        page = 0
        while True:
            screen.clear()  # ミッションや図鑑から戻ったときも、トップページだけを表示する
            page, pages = _show_menu(console, screen.rows, missions, progress, zukan, mode, page)
            choice = _choose(screen, console, missions, progress, paged=pages > 1)
            if choice is None:
                return goodbye()
            if choice == ZUKAN:
                zukan.browse(console, screen)
            elif choice == TUTORIAL:
                tutorial.run(screen, console, mode)
            elif choice == SENPAI:
                tour.room(screen, console, tours, missions, progress, mode, seed=args.seed)
            elif choice == REDRAW:
                pass
            elif isinstance(choice, str):  # ページを送る
                page += 1 if choice == NEXT_PAGE else -1
            else:
                screen.clear()  # トップページの表示を消して、このミッションの内容だけにする
                # 終了(exit)を選べるのはトップページだけ。ミッションを離れたら、必ずここへ戻る
                MissionSession(choice, console, mode, progress, zukan, seed=args.seed, screen=screen).play()
