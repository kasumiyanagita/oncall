"""ミッション「消えた注文」: 書式の違う 2 つのログを突き合わせ、片方にしかない ID を取り出す。

gateway.log で受け付けられた(status=202)checkout のうち、payment.csv に記録のない
リクエスト ID が答え。次のものを混ぜてある。

- 同じリクエストが 2 回記録されたリトライ      (重複の除去が必要)
- gateway が 400 で拒否した checkout            (決済に届かないのが正しい)
- checkout 以外のリクエスト                     (決済ログに無くて当然)
- payment.csv の見出し行と、時間帯の違う決済    (そのまま比べると混ざる)
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from oncall.mission import CheckResult

WORKDIR = "incident"
_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_OTHER_PATHS = ("/api/v1/cart", "/api/v1/items", "/api/v1/orders", "/api/v1/checkout/status", "/healthz")


def _clock(second: int) -> str:
    total = 1 * 3600 + 30 * 60 + second
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def generate(home: Path, rng: random.Random) -> dict:
    workdir = home / WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    ids = iter(rng.sample(range(16**8), 1200))

    def new_id() -> str:
        return f"{next(ids):08x}"

    accepted = [new_id() for _ in range(rng.randint(280, 340))]
    rejected = [new_id() for _ in range(rng.randint(18, 30))]
    lost = rng.sample(accepted, rng.randint(14, 24))
    # リトライで 2 回記録されるリクエスト。消えた注文も必ず含める
    retried = set(rng.sample(lost, 3)) | set(rng.sample(accepted, 25))

    window = 40 * 60
    gateway: list[tuple[int, str]] = []
    other_ids: list[str] = []

    def log(second: int, method: str, path: str, request_id: str, status: int) -> None:
        line = (
            f"2026-10-10T{_clock(second)}+09:00 gw-{rng.randint(1, 3)} {method} {path} "
            f"req={request_id} user=u{rng.randint(10000, 99999)} status={status}"
        )
        gateway.append((second, line))

    accepted_at: dict[str, int] = {}
    for request_id in accepted:
        second = rng.randrange(window)
        accepted_at[request_id] = second
        log(second, "POST", "/api/v1/checkout", request_id, 202)
        if request_id in retried:
            log(min(window - 1, second + rng.randint(1, 5)), "POST", "/api/v1/checkout", request_id, 202)
    for request_id in rejected:
        log(rng.randrange(window), "POST", "/api/v1/checkout", request_id, 400)
    for _ in range(700):
        request_id = new_id()
        other_ids.append(request_id)
        log(rng.randrange(window), "GET", rng.choice(_OTHER_PATHS), request_id, rng.choice((200, 200, 200, 304, 404)))

    gateway.sort(key=lambda entry: entry[0])
    (workdir / "gateway.log").write_text("\n".join(line for _, line in gateway) + "\n")

    payments: list[tuple[int, str]] = []
    lost_set = set(lost)
    for request_id in accepted:
        if request_id in lost_set:
            continue
        second = accepted_at[request_id] + rng.randint(1, 4)
        result = rng.choices(("SUCCESS", "DECLINED"), (9, 1))[0]
        payments.append((second, f"2026-10-10 {_clock(second)},{request_id},{result},{rng.randint(5, 900) * 100}"))
    # 調査対象の時間帯より前の決済(gateway.log には対応する行がない)
    for _ in range(40):
        second = -rng.randint(1, 600)
        payments.append((second, f"2026-10-10 {_clock(second)},{new_id()},SUCCESS,{rng.randint(5, 900) * 100}"))
    payments.sort(key=lambda entry: entry[0])
    (workdir / "payment.csv").write_text(
        "\n".join(["processed_at,request_id,result,amount", *(line for _, line in payments)]) + "\n"
    )

    return {
        "answer": sorted(lost),
        "rejected": rejected,
        "retried": sorted(retried),
        "other_ids": other_ids,
        "incident_id": f"INC-{rng.randint(1000, 9999)}",
        "detected_at": "02:14",
    }


def check(home: Path, ctx: dict) -> CheckResult:
    path = home / WORKDIR / "answer.txt"
    if not path.is_file():
        return CheckResult(False, "`~/incident/answer.txt` がまだありません。")

    found = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]
    if not found:
        return CheckResult(False, "`answer.txt` が空です。")
    if "request_id" in found:
        return CheckResult(False, "`payment.csv` の見出し行(`request_id`)が混ざっています。1 行目は見出しです。")
    malformed = [line for line in found if not _ID_RE.match(line)]
    if malformed:
        return CheckResult(
            False,
            f"リクエスト ID だけを 1 行に 1 つ書いてください(`req=` などは付けません)。問題の行: `{malformed[0]}`",
        )

    answer = set(ctx["answer"])
    unique = set(found)
    if unique & set(ctx["rejected"]):
        return CheckResult(
            False,
            "gateway が拒否した(`status=400`)リクエストが混ざっています。対象は、受け付けられた `status=202` の checkout だけです。",
        )
    if unique & set(ctx["other_ids"]):
        return CheckResult(False, "checkout 以外のリクエストが混ざっています。対象は `POST /api/v1/checkout` だけです。")
    extra = unique - answer
    if extra and extra <= set(ctx["retried"]):
        return CheckResult(
            False,
            f"決済済みの ID が {len(extra)} 件混ざっています。どれも、リトライで gateway に 2 回記録されたリクエストです。"
            "比べる前に、それぞれの一覧から重複を除いてください。",
        )
    if extra:
        return CheckResult(False, f"決済ログに記録のある ID が {len(extra)} 件混ざっています。")
    missing = answer - unique
    if missing:
        return CheckResult(False, f"消えた注文はあと {len(missing)} 件あります(いま {len(unique)} 件)。")
    if len(found) != len(unique):
        return CheckResult(
            False,
            "ID は合っていますが、同じ ID が複数回書かれています。"
            "リトライで同じリクエストが 2 回記録されているものがあります。重複を除いてください。",
        )
    return CheckResult(True)
