"""クリア時のスコアとランクの計算。"""

from __future__ import annotations

from dataclasses import dataclass

RANKS = ((95, "S"), (80, "A"), (60, "B"))
LOWEST_RANK = "C"

FAILED_CHECK_COST = 5
FAILED_CHECK_CAP = 20
TIME_CAP = 20
COMMANDS_CAP = 15


@dataclass(frozen=True)
class Score:
    points: int
    rank: str
    elapsed: float
    commands: int
    hints: int
    failed_checks: int
    penalties: tuple[tuple[str, int], ...]


def evaluate(
    *,
    par_time: int,
    par_commands: int,
    elapsed: float,
    commands: int,
    hint_costs: list[int],
    failed_checks: int,
) -> Score:
    """100 点満点からの減点方式。目安(par)を超えた分だけ引く。"""
    penalties: list[tuple[str, int]] = []

    if hint_costs:
        penalties.append((f"ヒント {len(hint_costs)} 個", sum(hint_costs)))
    if failed_checks:
        penalties.append(
            (f"判定ミス {failed_checks} 回", min(FAILED_CHECK_CAP, failed_checks * FAILED_CHECK_COST))
        )
    # 目安時間の 2 倍かかると上限まで減点
    over_time = max(0.0, elapsed - par_time)
    if time_penalty := min(TIME_CAP, int(over_time / par_time * TIME_CAP)):
        penalties.append(("目安時間オーバー", time_penalty))
    if command_penalty := min(COMMANDS_CAP, max(0, commands - par_commands)):
        penalties.append(("目安コマンド数オーバー", command_penalty))

    points = max(0, 100 - sum(p for _, p in penalties))
    rank = next((r for threshold, r in RANKS if points >= threshold), LOWEST_RANK)
    return Score(
        points=points,
        rank=rank,
        elapsed=elapsed,
        commands=commands,
        hints=len(hint_costs),
        failed_checks=failed_checks,
        penalties=tuple(penalties),
    )
