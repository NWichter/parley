import re
from pathlib import Path

from conftest import buyer_msg, supplier_msg

from parley import policy as pol_mod
from parley.dossier import Dossier
from parley.policy import CasePolicy

RFQ_STD = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
A0 = {"unit_price": 18.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}


def _threads():
    ask = {**A0, "unit_price": 15.0}
    return {
        "sup:A": [supplier_msg("sup:A", 0, A0, standard=RFQ_STD), buyer_msg([ask]),
                  supplier_msg("sup:A", 1, {**A0, "unit_price": 15.3})],
        "sup:B": [supplier_msg("sup:B", 0, {**A0, "unit_price": 19.0}, standard=RFQ_STD)],
    }


def test_g2_rfq_take_closes_the_case_at_once(rfq_case, monkeypatch):
    for k in [k for k in __import__("os").environ if k.startswith("PARLEY_")]:
        monkeypatch.delenv(k, raising=False)
    pol = CasePolicy(rfq_case, Dossier(rfq_case, None))
    threads = _threads()
    d = pol.decide("sup:A", threads)
    assert d.kind == "done" and d.metrics.get("take")
    assert pol_mod.flag("PARLEY_RFQ_CLOSE_NOW")
    assert pol.take_now("sup:A", threads, d)
    assert not pol.take_now("sup:B", threads, d)


def test_safe_env_turns_off_every_new_switch():
    src = Path(__file__).resolve().parents[2].joinpath("ops", "final.py").read_text(encoding="utf-8")
    safe = src[src.index("SAFE_ENV = {"):]
    safe = safe[: safe.index("}") + 1]
    assert '"PARLEY_RULES": "0"' in safe
    from parley import pacing, runner

    assert pacing.P90_RATIO == 1.5 or "PARLEY_PACE_P90_RATIO" in safe
    assert runner.LAND_S == runner.CLOSE_BEFORE_CAP_S or "PARLEY_PACE_LAND_S" in safe


def test_clause_ask_is_read_from_the_suppliers_mail():
    assert pol_mod.CLAUSE_ASK.search("A price adjustment clause on PPI-C241 would let us price much sharper.")
    assert pol_mod.CLAUSE_ASK.search("that is exactly why we ask for a price adjustment clause.")
    assert not pol_mod.CLAUSE_ASK.search("We need a price adjustment of 6% from January.")
