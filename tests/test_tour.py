"""見学(先輩の作業を見る): 先輩の打つコマンドが、どの乱数の種でも実際に動き、話と食い違わないこと。"""

import random
import subprocess

import pytest

from conftest import MODE
from oncall import procs, sandbox
from oncall.mission import load_missions, render
from oncall.tour import load_tours, plain

MISSIONS = load_missions()
TOURS = {tour.id: tour for tour in load_tours(MISSIONS)}
SEEDS = range(4)
# 画面に何も出ないのが正しい手(ファイルへ書く、書き換える、止める)
SILENT = (" > ", "cp -r ", "sed -i ", "kill ", "chmod ")


def watch(tour_id: str, base, seed: int) -> tuple[dict, list[str]]:
    """見学の全部の手を、シェルの中で順に実行する。(生成された値, 手ごとの出力) を返す。"""
    tour = TOURS[tour_id]
    layout = sandbox.Layout(base)
    layout.create()
    session = f"test{random.getrandbits(48):012x}"
    ctx = tour.scenario.generate(layout.home, random.Random(seed))
    ctx["session"] = session

    lines = []
    if setup := getattr(tour.scenario, "shell_setup", None):
        (layout.run / "setup.bash").write_text(setup(ctx))
        lines += ['. "$ONCALL_RUN/setup.bash"', "sleep 2"]
    lines += ['cd "$ONCALL_WORKDIR" || exit 99', "prev="]
    for number, step in enumerate(tour.steps, start=1):
        # {{prev}}(直前のコマンドの出力)は、シェルの変数として渡す
        command = render(step.run, {**ctx, "prev": "$prev"})
        lines += [f"prev=$({command})", f'echo $? > "$ONCALL_RUN/status.{number}"', f'echo "$prev" > "$ONCALL_RUN/out.{number}"']
    (layout.run / "script.sh").write_text("\n".join(lines) + "\n")
    argv, env, cwd = sandbox.shell_command(
        MODE, layout, host=tour.host, workdir=tour.workdir, session=session, command=["bash", "-c", 'bash "$ONCALL_RUN/script.sh"']
    )
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    finally:
        procs.kill_session(session)
    assert result.returncode == 0, result.stderr

    outputs = []
    for number, step in enumerate(tour.steps, start=1):
        status = int((layout.run / f"status.{number}").read_text())
        output = (layout.run / f"out.{number}").read_text().strip()
        # diff は、違いがあると 1 で終わる(失敗ではない)
        assert status == 0 or (step.run.startswith("diff ") and status == 1), f"{number} 番目の手が失敗した: {step.run}"
        if any(mark in step.run or step.run.startswith(mark) for mark in SILENT):
            assert not output, f"{number} 番目の手は、何も出さないはず: {step.run}"
        else:
            assert output, f"{number} 番目の手が、何も出さなかった: {step.run}"
        outputs.append(output)
    return ctx, outputs


def tours_without_processes():
    return [tour_id for tour_id, tour in TOURS.items() if not hasattr(tour.scenario, "shell_setup")]


def test_tours_are_complete():
    assert [tour.technique for tour in TOURS.values()] == ["数える", "探す", "比べる", "書き換える", "止める", "まとめる"]
    for tour in TOURS.values():
        assert tour.title and tour.intro and tour.outro and tour.commands and tour.helps
        assert len(tour.steps) >= 5
        assert all(step.say and step.run and step.note for step in tour.steps)


def test_tours_do_not_reuse_the_incidents():
    """見学の題材は、インシデントとは別に用意する(答えやヒントと重ならないように)。"""
    for tour in TOURS.values():
        assert all(tour.host != mission.host for mission in MISSIONS)
        solutions = " ".join(mission.solution for mission in tour.helps)
        assert all(step.run not in solutions for step in tour.steps)


def test_plain_removes_control_sequences():
    assert plain("\x1b[32m1234\x1b[0m\r\n") == "1234\n"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("tour_id", tours_without_processes())
def test_every_command_in_the_tour_works(tour_id, seed, tmp_path):
    watch(tour_id, tmp_path, seed)


@pytest.mark.parametrize("seed", SEEDS)
def test_count_finds_the_domain_that_bounces(seed, tmp_path):
    ctx, outputs = watch("count", tmp_path, seed)
    assert outputs[-1].splitlines()[0].split()[1] == ctx["bad_domain"]


@pytest.mark.parametrize("seed", SEEDS)
def test_find_shows_what_the_senior_says(seed, tmp_path):
    ctx, outputs = watch("find", tmp_path, seed)
    assert len(outputs[1].splitlines()) == 2  # 昨夜の分は、db と files の 2 つ
    small = outputs[3].splitlines()
    assert len(small) == 2 and any(ctx["earlier_day"] in line for line in small)
    assert len(outputs[4].splitlines()) == 2 and ".part" in outputs[4]
    assert len(outputs[5].splitlines()) == 2


@pytest.mark.parametrize("seed", SEEDS)
def test_compare_finds_who_left_and_who_joined(seed, tmp_path):
    ctx, outputs = watch("compare", tmp_path, seed)
    assert "、".join(outputs[5].split()) == ctx["left"]
    assert "、".join(outputs[6].split()) == ctx["joined"]
    assert int(outputs[7]) == ctx["both_count"]


@pytest.mark.parametrize("seed", SEEDS)
def test_rewrite_changes_only_the_log_level(seed, tmp_path):
    ctx, outputs = watch("rewrite", tmp_path, seed)
    assert "debug_toolbar" in outputs[0] and "debug_toolbar" not in outputs[1]
    changed = [line for line in outputs[5].splitlines() if line[:1] in "<>"]
    assert len(changed) == ctx["debug_count"] * 2 and all("log_level" in line for line in changed)
    assert "debug" not in outputs[6]


@pytest.mark.parametrize("seed", SEEDS)
def test_script_reports_a_missing_directory(seed, tmp_path):
    ctx, outputs = watch("script", tmp_path, seed)
    assert int(outputs[0]) == int(outputs[2]) == int(outputs[-1]) == ctx["waiting"]
    assert int(outputs[3]) == ctx["sent"]
    assert outputs[4] == "1"  # [ -d nowhere ] は失敗(1)で終わる
    assert outputs[5].split() == ["0", "0"]  # 直す前は、場所がなくても「0 件、成功」と答えてしまう
    assert outputs[9] == "2"  # 直したあとは、失敗(2)で終わる


@pytest.mark.skipif(MODE != sandbox.ISOLATED, reason="プロセスを扱うので、隔離モードが使える環境でだけ実行する")
@pytest.mark.parametrize("seed", SEEDS)
def test_stop_stops_only_the_wrong_one(seed, tmp_path):
    ctx, outputs = watch("stop", tmp_path, seed)
    assert len(outputs[1].splitlines()) == 2
    assert outputs[3].isdigit()  # 止める相手の番号は、1 つだけ
    after = outputs[5].splitlines()
    assert len(after) == 1 and after[0].endswith(ctx["right_date"])
