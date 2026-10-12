"""ミッション「深夜の 500 祭り」: 雑な数え方では通らないことを多数の種で確かめる。"""

import random
import subprocess

import pytest

from oncall.mission import load_missions

SEEDS = range(40)


@pytest.fixture(scope="module")
def mission():
    return next(m for m in load_missions() if m.id == "error-ips")


def _run(command: str, cwd) -> None:
    subprocess.run(["bash", "-c", command], cwd=cwd, check=True)


def _setup(mission, tmp_path, seed):
    ctx = mission.scenario.generate(tmp_path, random.Random(seed))
    return ctx, tmp_path / mission.workdir


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "naive",
    [
        "grep 500 access.log",
        "grep ' 500 ' access.log",
        "awk '$9 >= 500' access.log",
        "cat access.log",
    ],
)
def test_naive_counting_fails(mission, tmp_path, seed, naive):
    ctx, workdir = _setup(mission, tmp_path, seed)
    _run(f"{naive} | awk '{{print $1}}' | sort | uniq -c | sort -rn | head -3 > answer.txt", workdir)
    result = mission.scenario.check(tmp_path, ctx)
    assert not result.ok
    assert result.message


def test_wrong_order_is_reported(mission, tmp_path):
    ctx, workdir = _setup(mission, tmp_path, 1)
    (workdir / "answer.txt").write_text("\n".join(reversed(ctx["answer"])) + "\n")
    result = mission.scenario.check(tmp_path, ctx)
    assert not result.ok
    assert "並び順" in result.message


def test_same_seed_gives_same_log(mission, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ctx_a = mission.scenario.generate(a, random.Random(7))
    ctx_b = mission.scenario.generate(b, random.Random(7))
    assert ctx_a["answer"] == ctx_b["answer"]
