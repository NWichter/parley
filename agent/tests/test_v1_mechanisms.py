"""Candidate switches from the v1 comparison (default off). Each test shows the behaviour the switch
changes: without it the old behaviour, with it the new one."""

import pytest
from conftest import buyer_msg, supplier_msg

from parley.dossier import Dossier
from parley.policy import CasePolicy, trailing_moves

FLAGS = ("PARLEY_INDEX_VALUE", "PARLEY_HEDGE_NOCLAUSE", "PARLEY_V1_INDEX", "PARLEY_V1_RFQ")
STD_C = {"payment_days": 30, "contract_months": 12, "index_clause": None}
OPEN_C = {"unit_price": 6.2, "payment_days": 30, "contract_months": 12, "index_clause": None}


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in FLAGS:
        monkeypatch.setenv(f, "0")


def policy(case):
    return CasePolicy(case, Dossier(case, None))


def clause_of(sheet):
    return sheet.get("index_clause")


def first_sheets(case, sid="sup:C", opening=OPEN_C, std=STD_C, body=None, full_cost=None):
    pol = policy(case)
    if full_cost:
        pol.dossier.profiles[sid].full_cost = full_cost
    kw = {"body": body} if body else {}
    threads = {sid: [supplier_msg(sid, 0, opening, standard=std, **kw)]}
    d = pol.decide(sid, threads)
    assert d.kind == "send"
    return pol, d.sheets


# ---- PARLEY_INDEX_VALUE ---------------------------------------------------------------------------


def test_trailing_moves_reads_the_suppliers_twelve_month_moves():
    body = "AS-X rose 5.9 % in twelve months and is 45 % of our cost. LCI-C fell 2,5 % in twelve months."
    assert trailing_moves({"body": body}) == pytest.approx({"AS-X": 0.059, "LCI-C": -0.025})


def test_index_value_estimates_a_missing_path_from_the_stated_move(clause_case, monkeypatch):
    clause_case["buyer"]["index_paths"] = {}
    body = "Guten Tag,\nAS-X rose 30.0 % in twelve months and is 45 % of our cost.\nGruesse"
    pol, _ = first_sheets(clause_case, body=body)
    assert "AS-X" not in pol.val.buyer["index_paths"]
    monkeypatch.setenv("PARLEY_INDEX_VALUE", "1")
    pol, _ = first_sheets(clause_case, body=body)
    assert pol.val.buyer["index_paths"]["AS-X"] == pytest.approx(0.08)
    assert clause_case["buyer"]["index_paths"] == {}


def test_index_value_a_richer_clause_must_be_cheaper_for_us(clause_case, monkeypatch):
    """Rising index: a sheet that carries a costlier clause than another must cost us less than it
    (the supplier takes the clause and then only talks price)."""
    clause_case["buyer"]["index_paths"] = {"AS-X": 0.06}

    def violations(pol, sheets):
        out = []
        for a in sheets:
            for b in sheets:
                extra = pol.clause_cost(a, STD_C) - pol.clause_cost(b, STD_C)
                if extra > 1 and pol.cost("sup:C", a, STD_C) >= pol.cost("sup:C", b, STD_C):
                    out.append((a, b))
        return out

    pol, sheets = first_sheets(clause_case)
    assert violations(pol, sheets)
    monkeypatch.setenv("PARLEY_INDEX_VALUE", "1")
    pol, sheets = first_sheets(clause_case)
    assert not violations(pol, sheets)


def _indexed_round2(case, db_standing):
    clause = {"series": "AS-X", "material_share": 0.45, "deadband_pct": db_standing, "base_month": "2026-08"}
    stand = {"unit_price": 5.0, "payment_days": 30, "contract_months": 24, "index_clause": clause}
    ask = {**stand, "unit_price": 4.99}
    return {
        "sup:C": [
            supplier_msg("sup:C", 0, {**stand, "unit_price": 6.2}, standard=STD_C),
            buyer_msg([{**stand, "unit_price": 4.6}]),
            supplier_msg("sup:C", 1, stand),
            buyer_msg([ask]),
            supplier_msg("sup:C", 2, stand),
        ]
    }


def test_index_value_asks_for_the_wider_deadband_before_settling(clause_case, monkeypatch):
    clause_case["buyer"]["index_paths"] = {"AS-X": 0.06}
    d = policy(clause_case).decide("sup:C", _indexed_round2(clause_case, 0))
    assert d.kind == "done"
    monkeypatch.setenv("PARLEY_INDEX_VALUE", "1")
    d = policy(clause_case).decide("sup:C", _indexed_round2(clause_case, 0))
    assert d.kind == "send" and "deadband" in d.reason
    assert clause_of(d.sheets[0])["deadband_pct"] == 5


def test_index_value_does_not_ask_for_a_deadband_on_a_falling_index(clause_case, monkeypatch):
    clause_case["buyer"]["index_paths"] = {"AS-X": -0.06}
    monkeypatch.setenv("PARLEY_INDEX_VALUE", "1")
    d = policy(clause_case).decide("sup:C", _indexed_round2(clause_case, 0))
    assert d.kind == "done"


def test_index_value_lever_offers_the_cheapest_clause_not_the_last(clause_case, monkeypatch):
    opts = clause_case["term_options"]["index_clause"]
    clause_case["term_options"]["index_clause"] = [opts[0], opts[2], opts[1]]
    clause_case["negotiable"] = ["unit_price", "index_clause"]
    clause_case["buyer"]["index_paths"] = {"AS-X": 0.06}
    threads = {"sup:C": [supplier_msg("sup:C", 0, OPEN_C, standard=STD_C)]}

    def lever():
        pol = policy(clause_case)
        pol.model("sup:C", threads["sup:C"])
        v = pol.view("sup:C", threads["sup:C"])
        return pol.unused_lever("sup:C", v, pol.value("sup:C", v.terms))

    assert lever()[1]["index_clause"]["deadband_pct"] == 0
    monkeypatch.setenv("PARLEY_INDEX_VALUE", "1")
    assert lever()[1]["index_clause"]["deadband_pct"] == 5


# ---- PARLEY_HEDGE_NOCLAUSE ------------------------------------------------------------------------


def _health_clause_case(case):
    case["type"] = "price_increase"
    case["negotiable"] = ["unit_price", "payment_days", "index_clause"]
    case["fixed_terms"]["contract_months"] = 12
    case["buyer"].update(
        category_goal="stabilise",
        horizon_years=1,
        sustainable_margin_pct=0.5,
        relationship_eur_per_point=2000,
        index_risk_pct=4,
        index_paths={"AS-X": 0.05},
        current_unit_price=5.6,
    )
    return case


def test_hedge_noclause_drops_a_round_one_clause_sheet_that_raises_our_cost(clause_case, monkeypatch):
    case = _health_clause_case(clause_case)
    std = {"payment_days": 30, "index_clause": None}
    opening = {"unit_price": 6.2, "payment_days": 30, "index_clause": None}

    def dearer_clause_sheets():
        pol, sheets = first_sheets(case, opening=opening, std=std, full_cost=6.0)
        c0 = pol.cost("sup:C", sheets[0], std)
        return [s for s in sheets[1:] if clause_of(s) != clause_of(sheets[0]) and pol.cost("sup:C", s, std) > c0]

    assert dearer_clause_sheets()
    monkeypatch.setenv("PARLEY_HEDGE_NOCLAUSE", "1")
    assert not dearer_clause_sheets()


def test_hedge_noclause_a_hedged_supplier_is_not_offered_a_clause(clause_case, monkeypatch):
    clause_case["type"] = "renewal"
    clause_case["negotiable"] = ["unit_price", "index_clause"]
    clause_case["buyer"]["index_paths"] = {"AS-X": 0.06}
    threads = {"sup:C": [supplier_msg("sup:C", 0, OPEN_C, standard=STD_C)]}

    def lever():
        pol = policy(clause_case)
        pol.dossier.profiles["sup:C"].hedged_series = ["AS-X"]
        pol.model("sup:C", threads["sup:C"])
        v = pol.view("sup:C", threads["sup:C"])
        return pol.unused_lever("sup:C", v, pol.value("sup:C", v.terms))

    assert lever() and lever()[0] == "index_clause"
    monkeypatch.setenv("PARLEY_HEDGE_NOCLAUSE", "1")
    assert lever() is None


# ---- PARLEY_V1_INDEX ------------------------------------------------------------------------------


def test_v1_index_no_alternative_carries_a_costlier_clause_than_the_main_sheet(clause_case, monkeypatch):
    case = _health_clause_case(clause_case)
    std = {"payment_days": 30, "index_clause": None}
    opening = {"unit_price": 6.2, "payment_days": 30, "index_clause": None}

    def richer():
        pol, sheets = first_sheets(case, opening=opening, std=std, full_cost=6.0)
        base = pol.clause_cost(sheets[0], std)
        return [s for s in sheets[1:] if pol.clause_cost({**s, "unit_price": sheets[0]["unit_price"]}, std) > base + 1]

    assert richer()
    monkeypatch.setenv("PARLEY_V1_INDEX", "1")
    assert not richer()


# ---- PARLEY_V1_RFQ --------------------------------------------------------------------------------

RFQ_STD = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}


def _rfq_threads():
    return {
        "sup:A": [
            supplier_msg("sup:A", 0, {"unit_price": 17.1, "payment_days": 30, "lead_time_days": 28}, standard=RFQ_STD)
        ],
        "sup:B": [
            supplier_msg("sup:B", 0, {"unit_price": 16.0, "payment_days": 45, "lead_time_days": 35}, standard=RFQ_STD)
        ],
    }


def test_v1_rfq_rival_goes_to_an_impatient_dearer_bidder_in_round_one(rfq_case, monkeypatch):
    def kinds(flag=None):
        if flag:
            monkeypatch.setenv("PARLEY_V1_RFQ", flag)
        pol = policy(rfq_case)
        pol.dossier.profiles["sup:A"].impatient = True
        return [c.kind for c in pol.decide("sup:A", _rfq_threads()).claims]

    assert "rival" not in kinds()
    assert "rival" not in kinds("depth")
    assert "rival" in kinds("rival")


def test_v1_rfq_depth_anchors_shallower(rfq_case, monkeypatch):
    def anchor():
        pol = policy(rfq_case)
        th = _rfq_threads()
        for s, ev in th.items():
            pol.model(s, ev)
        return pol.anchor_price("sup:A", pol.view("sup:A", th["sup:A"]))

    deep = anchor()
    monkeypatch.setenv("PARLEY_V1_RFQ", "depth")
    assert anchor() > deep
