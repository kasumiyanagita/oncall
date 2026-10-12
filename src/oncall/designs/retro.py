"""レトロのデザイン。緑一色のブラウン管の端末を手本にしている。

このファイルだけで完結している。通常のデザイン(normal.py)とは何も共有していないので、
通常のほうを変えてもここは変わらないし、ここを変えても通常のほうは変わらない。

考え方:
- 色はほぼ緑の濃淡だけ。目立たせたいところは色ではなく、明るさ(と太字)で示す。
- 背景に色は付けない(文字と背景を入れ替える「反転」も使わない)。下線も引かない。
- 例外は 2 つ。ヒントと指摘は琥珀色、復習は赤みのある橙。
- 記号と枠は、昔の端末で出せた文字(ASCII)だけを使う。フォントを選ばず、幅もずれない。
- フォントや背景の色、ブラウン管風の効果は端末の側の設定なので、下の TERMINAL に書く。
"""

from rich import box

NAME = "retro"

# ---- 色と文字の飾り ---------------------------------------------------------
STYLES = {
    # 打つもの: いちばん明るい文字
    "d.command": "bold bright_white",
    "d.logo": "bold bright_green",
    # 見出し: 明るい緑の太字
    "d.heading": "bold bright_green",
    # 枠の線と、枠の見出し
    "d.frame": "green",
    # 「コマンド一覧」の見出しは、枠の線と同じ色のままにして目立たせない
    "d.frame.title": "none",
    # コマンドの説明の枠の見出し(コマンド名の右に出る説明)
    "d.frame.caption": "bold bright_green",
    "d.frame.command": "bold bright_white",
    # ページの案内や、進み具合のいまの手
    "d.title": "bold bright_green",
    # インシデントの印: 赤みのある橙
    "d.incident": "bold red",
    # ランク
    "d.rank.s": "bold yellow",
    "d.rank.a": "bold bright_white",
    "d.rank.b": "bold bright_green",
    "d.rank.c": "green",
    # 図鑑に新しく登録されたコマンドに付ける「NEW」: 琥珀色
    "d.new": "bold yellow",
    # できたこと と、正解
    "d.done": "bright_green",
    "d.success": "bold bright_white",
    # 復習に関するもの
    "d.review": "bold red",
    # ヒントと指摘: 琥珀色
    "d.hint": "yellow",
    # 補足: 暗い緑
    "d.muted": "dim",
    "d.placeholder": "dim",
    "d.rule": "green",
    # キーの名前
    "d.key": "bold bright_white",
    # 文章の中の **強調**
    "d.strong": "bold bright_green",
    # 実行方式の表示。隔離モード(ふだんの状態)は、まわりの文字と同じにして目立たせない。簡易モードだけ琥珀色で知らせる
    "d.mode.isolated": "none",
    "d.mode.guard": "bold yellow",
    # 注意と誤りの知らせ
    "d.warn": "yellow",
    "d.error": "bold red",
    # 入力欄の先頭
    "d.prompt.menu": "bold bright_green",
    "d.prompt.host": "bold bright_green",
    "d.prompt.path": "bold bright_green",
    "d.prompt.mark": "bold bright_white",
    # 表示に残す、打った行: 背景は付けず、打った文字をいちばん明るくする
    "d.echo": "none",
    "d.echo.text": "bold bright_white",
}

# ---- 記号(ASCII だけ) ------------------------------------------------------
HEADING_MARK = ">>"
CHECK = "*"
DUE = "!"
NOTE_MARK = "->"
LEVEL_ON, LEVEL_OFF = "#", "-"
INCIDENT_MARK = "*"
CROSS = "x"
GAUGE_ON, GAUGE_OFF = "#", "-"
TITLE_DASH = "-"  # 枠の見出しで、コマンドの名前と説明のあいだに置く線
RULE = "="
SEPARATOR = "|"

# ---- 枠 ---------------------------------------------------------------------
BOX = box.ASCII

# ---- ロゴ -------------------------------------------------------------------
# バックスラッシュ(\)は使わない。MS Gothic など日本語のフォントでは、\ が円記号(¥)で表示されるため
LOGO = """
 ###   #   #   ###    ###   #      #
#   #  ##  #  #   #  #   #  #      #
#   #  # # #  #      #####  #      #
#   #  #  ##  #   #  #   #  #      #
 ###   #   #   ###   #   #  #####  #####
"""

# ---- ロゴの下に出す行 -------------------------------------------------------
# レトロでは、ロゴのすぐ下にインシデントの一覧を置く。説明の行も、実行方式の行も出さない
TAGLINE = ""
SHOW_MODE = False

# ---- カーソル ---------------------------------------------------------------
# 形は端末の側(下の TERMINAL)で決めるので、アプリからは指定しない
CURSOR = ""

# ---- 端末の設定(Windows Terminal のプロファイルになる) ----------------------
TERMINAL = {
    "font": {"face": "MS Gothic", "size": 14},
    # 昔のパソコンのような、下から塗る四角いカーソル。cursorHeight は高さ(文字の高さに対する %。25〜100)
    "cursorShape": "vintage",
    "cursorHeight": 80,
    # ブラウン管風の走査線と、文字のにじみ
    "experimental.retroTerminalEffect": True,
    # 配色。黒に近い背景に、緑の文字。色の名前(green など)が実際にどの色になるかを、ここで決める
    "colors": {
        "background": "#06100A",
        "foreground": "#41D85A",
        "cursorColor": "#7CFF8E",
        "selectionBackground": "#1F6B33",
        "black": "#06100A",
        "red": "#F2784B",
        "green": "#41D85A",
        "yellow": "#FFB000",
        "blue": "#3FA66B",
        "purple": "#6FD08A",
        "cyan": "#58E0A0",
        "white": "#9CF0A8",
        "brightBlack": "#2E7D43",
        "brightRed": "#FF9566",
        "brightGreen": "#7CFF8E",
        "brightYellow": "#FFC940",
        "brightBlue": "#62D694",
        "brightPurple": "#98EBB0",
        "brightCyan": "#8CF5C0",
        "brightWhite": "#DFFFE4",
    },
}
