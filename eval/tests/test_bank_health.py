"""The bank's health-aware score: unchanged on non-relationship cases, bounded on relationship ones.

Needs the arena data package (ARENA_DATA or one of bank.DATA_CANDIDATES); skipped without it.
Run: uv run --with pytest --with fastapi --with uvicorn pytest eval/tests -q
"""

import os
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))

from bank import DATA_CANDIDATES  # noqa: E402
from cases import World, generate  # noqa: E402
from kit.cost import RELATIONSHIP_GOALS  # noqa: E402
from personas import PERSONAS  # noqa: E402
from server import BankSim  # noqa: E402


def _data():
    for cand in (os.environ.get("ARENA_DATA"), *DATA_CANDIDATES):
        if cand and (Path(cand) / "facts.json").is_file():
            return Path(cand)
    pytest.skip("no arena data package")


@pytest.fixture(scope="module")
def bank():
    world = World.load(_data())
    generated = generate(world, 20, 2026)
    return world, generated


def _results(world, generated, persona, how):
    sim = BankSim(world, generated, tag="t", no_clock=True)
    out = []
    for n, cid in enumerate(generated):
        team = f"team{persona}{n}"
        sim.bind(team, team, persona, 0)
        s = sim.open(team, cid)
        sess = sim.sessions[s["session_id"]]
        sup = sess["case"]["suppliers"][0]
        if how == "accept":
            sheet = sess["threads"][sup]["offer_id"]
            sim.message(team, s["session_id"], sup, {"action": {"type": "accept", "term_sheet_id": sheet}})
        else:
            sim.message(team, s["session_id"], sup, {"action": {"type": "walk_away"}})
        out.append((sess["case"], sess["result"]))
    return out


@pytest.mark.parametrize("persona", list(PERSONAS))
@pytest.mark.parametrize("how", ["accept", "walk"])
def test_other_cases_score_exactly_as_before(bank, persona, how):
    world, generated = bank
    rel = 0
    for case, r in _results(world, generated, persona, how):
        if case["buyer"]["category_goal"] in RELATIONSHIP_GOALS:
            rel += 1
            assert 0.0 <= r["score"] <= 1.0
            assert r["c_best"] >= r["c_best_plain"] - 0.01
            if r["deal"]:
                assert r["c_deal"] >= r["c_best"] - 0.01
            continue
        assert r["score"] == r["score_plain"]
        assert r["c_open"] == r["c_open_plain"]
        assert r["c_best"] == r["c_best_plain"]
        assert r["c_deal"] == r["c_deal_plain"]
    assert 0 < rel < len(generated)


def test_health_best_cost_equals_plain_off_relationship_goals(bank):
    world, generated = bank
    for cid, (case, hidden) in generated.items():
        p = PERSONAS["mirror"](case, hidden, case["suppliers"][0], 0, world, 0)
        if case["buyer"]["category_goal"] not in RELATIONSHIP_GOALS:
            assert p.best_cost(True) == p.best_cost(False)
        assert p.full_cost > 0
