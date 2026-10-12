"""チュートリアル。ゲームの進め方を、数ページで案内する。

初めて起動したときに 1 回だけ自動で出す。あとからは、トップページで `tutorial` と打てば見られる。
文章はどのデザインでも同じで、記号と色だけを、いま使っているデザインに合わせる。
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from oncall import design, sandbox
from oncall.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from oncall.screen import RESIZED, Screen

WORD = "tutorial"  # トップページで打つと、チュートリアルを開く言葉
NEXT, PREVIOUS = "next", "previous"
# 何も打っていないときの矢印キー。→ で次、← で前のページへ移る
KEYS = {"\x1b[C": NEXT, "\x1bOC": NEXT, "\x1b[D": PREVIOUS, "\x1bOD": PREVIOUS}


def _pages(mode: str) -> list[tuple[str, list[str | Text]]]:
    """(ページの見出し, 中身) の並び。中身の文字列は文章として折り返し、Text はそのまま出す。"""
    look = design.active
    safety = (
        "打ったコマンドは本物のシェルで動きます。ただし、ミッションごとに用意した使い捨ての場所の中だけで動くので、"
        "何を打っても、あなたのパソコンのファイルが変わることはありません。"
        if mode == sandbox.ISOLATED
        else "打ったコマンドは本物のシェルで動きます。作業用の場所の外は変更できないようにしてありますが、"
        "完全に切り離されてはいません(簡易モード)。作業は `~` の中だけで行ってください。"
    )
    rule = look.RULE
    screen_map = [
        Text.from_markup("  [d.muted](依頼の文章や、打ったコマンドの結果が、ここに流れます)[/]"),
        Text(),
        Text.from_markup(f"  [d.rule]{rule * 10}[/] [d.key]mission[/] 依頼 [d.rule]{look.SEPARATOR}[/] [d.key]check[/] 判定 [d.rule]{rule}[/]   [d.muted]使える言葉[/]"),
        Text.from_markup(f"  [d.incident]{look.INCIDENT_MARK}[/] [d.prompt.host]you@prod-api-01[/]:[d.prompt.path]~[/] [d.prompt.mark]$[/] ls        [d.muted]入力欄。ここに打って Enter を押します[/]"),
        Text.from_markup(f"  [d.rule]{rule * 32}[/]"),
        Text.from_markup(f"   [d.incident]{look.INCIDENT_MARK} INCIDENT[/] 深夜の 500 祭り       [d.muted]いま対応しているインシデント[/]"),
    ]
    return [
        (
            "ようこそ",
            [
                "oncall は、障害対応をこなしながら Linux のコマンドを身につけるゲームです。",
                "あなたは新人の SRE です。深夜にページャーが鳴り、壊れたサーバーに入って、本物のコマンドで原因を突き止めます。",
                safety,
            ],
        ),
        (
            "画面の見方",
            [
                "ミッション中の画面は、上から 3 つに分かれています。いちばん下に、いま対応しているインシデントが出たままになります。",
                *screen_map,
            ],
        ),
        (
            "進め方",
            [
                "1. トップページで番号を打つと、そのインシデントの対応が始まります。**Enter** だけなら、まだ解決していない最初のものが始まります。\n"
                "2. 最初に出る **依頼**(チケット)を読み、コマンドを打って調べます。使うコマンドに決まりはありません。\n"
                "3. 解決できたと思ったら `check` と打ちます。まだなら、何が足りないかが出ます。\n"
                "4. 解決すると、所要時間・コマンド数・ヒント・判定ミスから **ランク**(S / A / B / C)が付き、模範解答と別解が表示されます。",
                "依頼の先頭にある `INC-1234` は障害の管理番号、`SEV2` は重大度です(数字が小さいほど深刻)。"
                "現場の書き方をまねたもので、解くのには使いません。",
            ],
        ),
        (
            "困ったとき",
            [
                "ミッション中に、次の言葉を打てます。",
                "1. `mission` … 依頼をもう一度表示する\n"
                "2. `hint` … ヒントを 1 段階開く(減点あり)\n"
                "3. `zukan` … コマンド図鑑を見る(`zukan grep` で詳細)\n"
                "4. `title` … トップページに戻る\n"
                "5. `exit` … ゲームを終了する",
                f"このチュートリアルは、トップページで `{WORD}` と打つと、いつでも見られます。",
            ],
        ),
    ]


def _show(console: Console, title: str, blocks: list[str | Text], number: int, total: int) -> None:
    width = min(console.width, MAX_WIDTH)
    inner = width - PANEL_CHROME - 2
    body: list[Text] = []
    in_figure = False
    for block in blocks:
        figure = isinstance(block, Text)
        # 段落のあいだは 1 行空ける。図(Text が続く部分)の中は空けない
        if body and not (figure and in_figure):
            body.append(Text())
        if figure:
            block.no_wrap = True
            body.append(block)
        else:
            body.append(render_prose(block, inner))
        in_figure = figure
    console.print()
    console.print(
        Panel(
            Group(*body),
            title=f"[d.frame.caption]チュートリアル {number}/{total}  {title}[/]",
            title_align="left",
            box=design.active.BOX,
            border_style="d.frame",
            padding=(1, 2),
            width=width,
        )
    )


def run(screen: Screen, console: Console, mode: str) -> bool:
    """チュートリアルを最初のページから見せる。終了(exit)を選んだら False、それ以外は True。"""
    pages = _pages(mode)
    index = 0
    while True:
        screen.clear()
        title, blocks = pages[index]
        _show(console, title, blocks, index + 1, len(pages))
        last = index == len(pages) - 1
        screen.set_status(
            f"[d.key]Enter[/] {'はじめる' if last else '次へ'}",
            "[d.key]← →[/] ページ",
            "[d.key]title[/] トップページに戻る",
            "[d.key]exit[/] 終了",
        )
        answer = screen.read_line(
            shortcuts=KEYS,
            placeholder="Enter で次へ" if not last else "Enter でトップページへ",
            redraw_on_resize=True,
        )
        if answer == RESIZED:  # 端末の大きさが変わった。同じページを、新しい大きさで描き直す
            continue
        answer = None if answer is None else answer.strip().lower()
        if answer is None or answer == "exit":
            return False
        if answer == "title" or (answer == "" and last):
            return True
        if answer == PREVIOUS:
            index = max(0, index - 1)
        elif answer in {"", NEXT}:
            index = min(len(pages) - 1, index + 1)
