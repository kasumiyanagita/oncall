"""ミッション「深夜の 500 祭り」: アクセスログから 500 エラーの多い送信元 IP を特定する。

ログには、雑な数え方をすると答えがずれる「ひっかけ」を混ぜてある。

- レスポンスサイズが 500 バイトの正常リクエストを大量に送る IP  (`grep ' 500 '` 対策)
- URL に /500 を含むリクエストを大量に送る IP                  (`grep 500` 対策)
- 502 を大量に受け取っている IP                                (5xx をまとめて数える対策)
- 件数だけは最多のヘルスチェック用 IP                          (ステータスを見ない対策)
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "incident"
WINDOW_SECONDS = 45 * 60
ANSWER_SIZE = 3

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CLIENT_PREFIXES = ("203.0.113", "198.51.100", "192.0.2", "172.20.14", "172.20.31")
_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "okhttp/4.12.0",
    "python-requests/2.32.3",
    "curl/8.5.0",
)
_NORMAL_PATHS = (
    "/api/v1/orders",
    "/api/v1/orders/{n}",
    "/api/v1/users/{n}",
    "/api/v1/items",
    "/api/v1/items/{n}",
    "/api/v1/cart",
    "/api/v1/search?q=item{n}",
    "/static/app.js",
    "/static/app.css",
    "/",
)
_NORMAL_STATUSES = (200, 201, 204, 301, 304, 400, 401, 403, 404)
_NORMAL_WEIGHTS = (70, 4, 3, 2, 8, 3, 3, 2, 5)

NGINX_CONF = """\
# /etc/nginx/nginx.conf より抜粋
http {
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';

    access_log /var/log/nginx/access.log main;
}
"""

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _unique_ips(rng: random.Random, count: int) -> list[str]:
    ips: set[str] = set()
    while len(ips) < count:
        ips.add(f"{rng.choice(_CLIENT_PREFIXES)}.{rng.randint(2, 254)}")
    return sorted(ips, key=lambda _: rng.random())


def _size(rng: random.Random) -> int:
    # ちょうど 500 バイトになるのは「ひっかけ」用の IP だけにする
    size = rng.randint(120, 9000)
    return size + 1 if size == 500 else size


def generate(home: Path, rng: random.Random) -> dict:
    workdir = home / WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    ips = _unique_ips(rng, 36)
    culprits = ips[:ANSWER_SIZE]
    size_decoy, url_decoy, gateway_decoy = ips[3:6]
    bystanders = ips[6:]
    monitor = "10.0.0.5"

    # (秒オフセット, IP, メソッド, パス, ステータス, サイズ, UA)
    entries: list[tuple[int, str, str, str, int, int, str]] = []

    def add(ip: str, method: str, path: str, status: int, size: int, *, late: bool = False) -> None:
        # late: 障害が始まった終盤 15 分に集中させる
        start = WINDOW_SECONDS * 2 // 3 if late else 0
        entries.append(
            (rng.randrange(start, WINDOW_SECONDS), ip, method, path, status, size, rng.choice(_AGENTS))
        )

    def add_normal(ip: str) -> None:
        path = rng.choice(_NORMAL_PATHS).format(n=rng.randint(1000, 9999))
        status = rng.choices(_NORMAL_STATUSES, _NORMAL_WEIGHTS)[0]
        add(ip, "GET", path, status, _size(rng))

    # 本命: 500 を大量に受け取っている 3 つの IP(件数に十分な差をつける)
    for ip, count in zip(culprits, (rng.randint(130, 170), rng.randint(80, 105), rng.randint(45, 62))):
        for _ in range(count):
            add(ip, "POST", "/api/v1/checkout", 500, rng.randint(140, 190), late=True)
        for _ in range(rng.randint(20, 60)):
            add_normal(ip)

    # ひっかけ
    for _ in range(rng.randint(220, 280)):
        add(size_decoy, "GET", f"/api/v1/items/{rng.randint(1000, 9999)}", 200, 500)
    for _ in range(rng.randint(180, 230)):
        add(url_decoy, "GET", "/api/v1/coupons/500", 404, _size(rng))
    for _ in range(rng.randint(110, 140)):
        add(gateway_decoy, "POST", "/api/v1/checkout", 502, rng.randint(150, 170), late=True)
    for _ in range(rng.randint(420, 480)):
        add(monitor, "GET", "/healthz", 200, 2)

    # その他の利用者: 普通のアクセスと、少数の 500 / 503 / 504
    for _ in range(2600):
        add_normal(rng.choice(bystanders))
    for ip in rng.sample(bystanders, 8):
        for _ in range(rng.randint(1, 14)):
            add(ip, "POST", "/api/v1/checkout", 500, rng.randint(140, 190), late=True)
    for ip in rng.sample(bystanders, 6):
        for _ in range(rng.randint(3, 20)):
            add(ip, "GET", "/api/v1/orders", rng.choice((503, 504)), rng.randint(150, 170), late=True)

    entries.sort(key=lambda e: e[0])

    detected = datetime.now().replace(hour=2, minute=rng.randint(10, 55), second=0, microsecond=0)
    window_start = detected - timedelta(seconds=WINDOW_SECONDS)
    lines = []
    for offset, ip, method, path, status, size, agent in entries:
        t = window_start + timedelta(seconds=offset)
        stamp = f"{t.day:02d}/{_MONTHS[t.month - 1]}/{t.year}:{t:%H:%M:%S} +0900"
        lines.append(f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} {size} "-" "{agent}"')

    (workdir / "access.log").write_text("\n".join(lines) + "\n")
    (workdir / "nginx.conf").write_text(NGINX_CONF)

    # 正解は書き出したデータから数え直す(生成ロジックと判定がずれないように)
    counts = Counter(e[1] for e in entries if e[4] == 500)
    answer = [ip for ip, _ in counts.most_common(ANSWER_SIZE)]

    return {
        "answer": answer,
        "string_decoys": [size_decoy, url_decoy, monitor],
        "gateway_decoy": gateway_decoy,
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": f"{detected:%H:%M}",
    }


def check(home: Path, ctx: dict) -> CheckResult:
    path = home / WORKDIR / "answer.txt"
    if not path.is_file():
        return CheckResult(False, "`~/incident/answer.txt` がまだありません。結果をファイルに保存してください。")

    lines = [line for line in path.read_text(errors="replace").splitlines() if line.strip()]
    if not lines:
        return CheckResult(False, "`answer.txt` が空です。")

    found: list[str] = []
    for line in lines:
        ips = _IP_RE.findall(line)
        if len(ips) != 1:
            return CheckResult(False, f"1 行に 1 つの IP アドレスを書いてください。問題の行: `{line.strip()}`")
        found.append(ips[0])

    answer: list[str] = ctx["answer"]
    if len(found) != len(answer):
        return CheckResult(False, f"上位 {len(answer)} 件を {len(answer)} 行で保存してください(いまは {len(found)} 行あります)。")
    if found == answer:
        return CheckResult(True)
    if sorted(found) == sorted(answer):
        return CheckResult(False, "3 つの IP は合っています。並び順(件数の多い順)を確認してください。")
    if set(found) & set(ctx["string_decoys"]):
        return CheckResult(
            False,
            "ステータスコードが 500 ではない IP が混ざっています。"
            "「500」という文字は、ステータスコード以外の場所にも現れていませんか?",
        )
    if ctx["gateway_decoy"] in found:
        return CheckResult(False, "500 以外のエラー(502 など)まで数えていませんか? 依頼は「ステータスコード 500」だけです。")
    correct = len(set(found) & set(answer))
    return CheckResult(False, f"{len(answer)} 件中 {correct} 件が正解です。数え方をもう一度確認してください。")
