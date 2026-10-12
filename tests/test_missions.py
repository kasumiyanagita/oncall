"""全ミッション共通: 何もしなければ不正解、模範解答と別解なら正解になること。"""

import pytest

from conftest import MISSIONS, MODE, play
from oncall import sandbox

SEEDS = range(4)


def _playable(mission_id):
    """プロセスを起動するミッションは、隔離モードが使えない環境では実行しない。"""
    if hasattr(MISSIONS[mission_id].scenario, "shell_setup") and MODE != sandbox.ISOLATED:
        pytest.skip("隔離モードが使える環境でだけ実行する")


@pytest.mark.parametrize("mission_id", MISSIONS)
def test_untouched_mission_is_not_solved(mission_id, tmp_path):
    _playable(mission_id)
    played = play(mission_id, tmp_path, 0, "true")
    assert not played.result.ok
    assert played.result.message


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("mission_id", MISSIONS)
def test_solution_solves_mission(mission_id, seed, tmp_path):
    _playable(mission_id)
    played = play(mission_id, tmp_path, seed, MISSIONS[mission_id].solution)
    assert played.result.ok, played.result.message


def _alternatives():
    return [
        pytest.param(mission.id, index, id=f"{mission.id}-{index}")
        for mission in MISSIONS.values()
        for index in range(len(mission.alternatives))
    ]


@pytest.mark.parametrize("seed", SEEDS[:2])
@pytest.mark.parametrize("mission_id, index", _alternatives())
def test_alternative_works(mission_id, index, seed, tmp_path):
    _playable(mission_id)
    alternative = MISSIONS[mission_id].alternatives[index]
    played = play(mission_id, tmp_path, seed, alternative.command)
    assert played.status == 0
    if alternative.partial:
        assert not played.result.ok  # 依頼の一部だけなので、まだ解決にはならない
    else:
        assert played.result.ok, played.result.message


def test_mission_ids_are_unique_and_ordered_by_level():
    missions = list(MISSIONS.values())
    assert len(MISSIONS) == 7
    assert [m.level for m in missions] == sorted(m.level for m in missions)
