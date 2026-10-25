import threading

from parley import rules
from parley.policy import depth_add, flag

CASE = {
    "type": "price_increase",
    "suppliers": ["sup:1"],
    "negotiable": ["unit_price"],
    "buyer": {"category_goal": "stabilise", "index_paths": {}},
}


def test_rules_turn_flags_on_per_case_and_env_wins(monkeypatch):
    monkeypatch.delenv("PARLEY_DEPTH_ADD", raising=False)
    monkeypatch.delenv("PARLEY_RULES", raising=False)
    rules.bind(rules.derive(CASE, {}))
    assert depth_add("stabilise") == 0.03
    assert depth_add("leverage") == 0.0
    monkeypatch.setenv("PARLEY_DEPTH_ADD", "stabilise:0.01")
    assert depth_add("stabilise") == 0.01
    rules.bind({})


def test_rules_off_switch_and_other_goals(monkeypatch):
    monkeypatch.setenv("PARLEY_RULES", "0")
    assert rules.derive(CASE, {}) == {}
    monkeypatch.delenv("PARLEY_RULES")
    lev = {**CASE, "buyer": {"category_goal": "leverage", "index_paths": {"CU": 0.05}}}
    assert rules.derive(lev, {}) == {}


def test_overrides_stay_on_their_own_thread(monkeypatch):
    monkeypatch.delenv("PARLEY_V1_INDEX", raising=False)
    rules.bind({"PARLEY_V1_INDEX": "1"})
    seen = []
    t = threading.Thread(target=lambda: seen.append(flag("PARLEY_V1_INDEX")))
    t.start()
    t.join()
    assert flag("PARLEY_V1_INDEX") is True and seen == [False]
    rules.bind({})
