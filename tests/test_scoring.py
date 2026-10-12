from oncall.scoring import evaluate


def _score(**overrides):
    args = dict(par_time=300, par_commands=10, elapsed=120, commands=5, hint_costs=[], failed_checks=0)
    return evaluate(**(args | overrides))


def test_clean_run_is_s_rank():
    score = _score()
    assert (score.points, score.rank, score.penalties) == (100, "S", ())


def test_one_hint_drops_below_s():
    assert _score(hint_costs=[10]).rank == "A"


def test_penalties_are_capped():
    score = _score(elapsed=10_000, commands=500, failed_checks=50)
    assert score.points == 100 - 20 - 15 - 20
    assert score.rank == "C"


def test_all_hints_and_slow_never_goes_negative():
    assert _score(hint_costs=[10, 15, 25], elapsed=10_000, commands=500, failed_checks=50).points == 0
