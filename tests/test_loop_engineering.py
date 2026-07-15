import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import loop_engineering as le  # noqa: E402


GOOD = {"semantics": 90, "geometry": 88, "framing": 86,
        "materials": 82, "lighting": 82, "realism": 80}


def test_weighted_score_is_bounded():
    assert 0 <= le.weighted_score(GOOD) <= 100


def test_missing_dimension_fails_loudly():
    scores = dict(GOOD)
    scores.pop("realism")
    try:
        le.weighted_score(scores)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_acceptance_requires_all_gates():
    state = le.new_state("x", target_score=80, min_dimension=70)
    state = le.record_iteration(state, GOOD, defects=[])
    assert state["status"] == "accepted"


def test_critical_defect_blocks_acceptance():
    state = le.new_state("x", target_score=80, min_dimension=70)
    state = le.record_iteration(state, GOOD, defects=[
        {"category": "semantics", "severity": "critical", "impact": 100,
         "fix": "restore missing runway"}
    ])
    assert state["status"] == "running"
    assert le.decision(state)["next_defect"]["category"] == "semantics"


def test_iterations_after_baseline_require_controlled_change():
    state = le.new_state("x")
    low = {key: 50 for key in le.DIMENSION_WEIGHTS}
    state = le.record_iteration(state, low)
    try:
        le.record_iteration(state, low)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_stagnation_restores_best_checkpoint():
    state = le.new_state("x", min_delta=2, patience=2)
    base = {key: 50 for key in le.DIMENSION_WEIGHTS}
    state = le.record_iteration(state, base)
    state = le.record_iteration(state, {key: 50.5 for key in base},
                                change={"category": "lighting"})
    state = le.record_iteration(state, {key: 50.6 for key in base},
                                change={"category": "materials"})
    verdict = le.decision(state)
    assert state["status"] == "stopped"
    assert verdict["action"] == "restore_best"


def test_best_iteration_survives_regression():
    state = le.new_state("x")
    base = {key: 40 for key in le.DIMENSION_WEIGHTS}
    state = le.record_iteration(state, base)
    better = {key: 70 for key in le.DIMENSION_WEIGHTS}
    state = le.record_iteration(state, better, change={"category": "geometry"})
    worse = {key: 60 for key in le.DIMENSION_WEIGHTS}
    state = le.record_iteration(state, worse, change={"category": "materials"})
    assert le.best_record(state)["index"] == 1


def test_state_roundtrip_is_atomic(tmp_path):
    state = le.new_state("x", references=["ref.png"])
    path = le.save_state(state, tmp_path / "loop_state.json")
    assert os.path.isfile(path)
    assert le.load_state(path) == state
