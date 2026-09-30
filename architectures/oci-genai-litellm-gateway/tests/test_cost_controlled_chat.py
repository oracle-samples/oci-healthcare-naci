import json

import pytest

from examples.cost_controlled_chat import choose_route, load_ledger, save_ledger


def test_auto_policy_defaults_to_basic_and_escalates_for_complexity():
    assert choose_route("Summarize this synthetic note.", "auto")[0] == "basic"
    assert choose_route("Analyze the trade-offs in this synthetic workflow.", "auto")[0] == "reasoning"
    assert choose_route("x" * 1_201, "auto")[0] == "reasoning"


@pytest.mark.parametrize("mode", ["basic", "reasoning"])
def test_explicit_route_wins(mode):
    assert choose_route("analyze a long multi-step task", mode) == (mode, "selected explicitly")


def test_ledger_contains_counters_only_and_resets_by_date(tmp_path):
    path = tmp_path / "usage.json"
    ledger = load_ledger(path, "2026-09-29")
    ledger["basic_calls"] = 2
    ledger["prompt_tokens"] = 31
    save_ledger(path, ledger)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_ledger(path, "2026-09-29") == ledger
    assert load_ledger(path, "2026-09-30")["basic_calls"] == 0
    serialized = path.read_text()
    assert set(json.loads(serialized)) == {
        "date", "basic_calls", "reasoning_calls", "prompt_tokens", "completion_tokens"
    }


def test_invalid_ledger_fails_closed(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text('{"date":"2026-09-29","basic_calls":"two"}')
    with pytest.raises(RuntimeError, match="invalid format"):
        load_ledger(path, "2026-09-29")
