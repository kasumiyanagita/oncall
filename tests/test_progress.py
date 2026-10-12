import json

from oncall.catalog import count_usage, load_catalog
from oncall.mission import load_missions
from oncall.progress import Progress
from oncall.scoring import evaluate


def _score(elapsed=120, hint_costs=()):
    return evaluate(
        par_time=300, par_commands=10, elapsed=elapsed, commands=5, hint_costs=list(hint_costs), failed_checks=0
    )


def test_clear_is_saved_and_reloaded(tmp_path):
    path = tmp_path / "data" / "progress.json"
    progress = Progress.load(path)
    outcome = progress.record_clear("m1", ["awk", "sort"], _score(hint_costs=[10]))
    assert outcome.new_commands == ("awk", "sort")
    assert outcome.previous_best is None and outcome.is_best

    reloaded = Progress.load(path)
    assert reloaded.best("m1") == progress.best("m1")
    assert reloaded.best("m1").penalties == (("ヒント 1 個", 10),)
    assert set(reloaded.unlocked) == {"awk", "sort"}
    assert reloaded.missions["m1"].clears == 1


def test_best_is_kept_and_commands_unlock_once(tmp_path):
    progress = Progress.load(tmp_path / "progress.json")
    progress.record_clear("m1", ["awk"], _score(elapsed=200))

    worse = progress.record_clear("m1", ["awk", "grep"], _score(hint_costs=[10]))
    assert not worse.is_best and worse.new_commands == ("grep",)
    assert progress.best("m1").points == 100

    faster = progress.record_clear("m1", ["awk"], _score(elapsed=60))
    assert faster.is_best and faster.previous_best.elapsed == 200
    assert progress.best("m1").elapsed == 60
    assert Progress.load(progress.path).missions["m1"].clears == 3


def test_usage_accumulates(tmp_path):
    progress = Progress.load(tmp_path / "progress.json")
    progress.add_usage({"awk": 2})
    progress.add_usage({"awk": 1, "sort": 4})
    assert Progress.load(progress.path).usage == {"awk": 3, "sort": 4}


def test_broken_save_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text("{ this is not json")
    progress = Progress.load(path)
    assert progress.missions == {} and progress.quarantined is not None
    assert progress.quarantined.read_text() == "{ this is not json"

    progress.record_clear("m1", ["awk"], _score())
    assert json.loads(path.read_text())["version"] == 1


def test_count_usage_reads_each_pipeline_stage():
    history = [
        "awk '$9 == 500 {print $1}' access.log | sort | uniq -c | sort -rn | head -3",
        "ls -l; /usr/bin/grep -c x f && head f",
        "echo $(grep x f)",
        "echo grep is not a command here",
        "check",
    ]
    assert count_usage(history, ["grep", "awk", "sort", "uniq", "head"]) == {
        "awk": 1, "sort": 2, "uniq": 1, "head": 2, "grep": 2,
    }


def test_every_catalog_entry_is_complete_and_taught_by_a_mission():
    catalog = load_catalog()
    taught = {c.name for m in load_missions() for c in m.commands}
    assert taught == set(catalog)
    for entry in catalog.values():
        assert entry.summary and entry.syntax and entry.description and entry.examples
