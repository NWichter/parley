"""Panel candidate switches (default off). Each test shows the old behaviour with the switch at 0
and the new one with it at 1."""

import pytest
from conftest import buyer_msg, supplier_msg

from parley.analyst import read_reply
from parley.dossier import Claim, Dossier
from parley.policy import CasePolicy

FLAGS = (
    "PARLEY_TYPE_DEPTH",
    "PARLEY_CASH_STRICT",
    "PARLEY_PI_CAP",
    "PARLEY_BUNDLE_PROOF",
    "PARLEY_QUALITY_ONE",
    "PARLEY_FREEZE_NARROW",
)
STOCK = "Guten Tag,\nFaster payment is welcome; we reflected it in the price.\nViele Gruesse"


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in FLAGS:
        monkeypatch.delenv(f, raising=False)
    for f in FLAGS[1:]:
        monkeypatch.setenv(f, "0")


def policy(case):
    return CasePolicy(case, Dossier(case, None))


# ---- PARLEY_TYPE_DEPTH ----------------------------------------------------------------------------


def _anchor(case):
    case["type"] = "bundling"
    opening = {"unit_price": 50.0, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
    pol = policy(case)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, opening)]}
    pol.model("sup:T1", threads["sup:T1"])
    return pol.anchor_price("sup:T1", pol.view("sup:T1", threads["sup:T1"]))


def test_type_depth_override_ablates_the_bundling_depth(case, monkeypatch):
    default = _anchor(case)
    monkeypatch.setenv("PARLEY_TYPE_DEPTH", "0.06")
    assert _anchor(case) == pytest.approx(default)
    monkeypatch.setenv("PARLEY_TYPE_DEPTH", "0")
    assert _anchor(case) == pytest.approx(default + 50.0 * 0.06)


# ---- PARLEY_CASH_STRICT ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,strict",
    [
        (STOCK, False),
        ("Faster payment would help us more than anything; we can reflect it in the price.", True),
        ("Our liquidity is tight this quarter.", True),
        ("We are on short-time work and our bank is watching.", True),
        ("We could grant a skonto of 2 % for payment within 10 days.", True),
        ("Shorter payment is fine with us, but the price stays.", False),
    ],
)
def test_cash_strict_needs_real_cash_evidence(body, strict, monkeypatch):
    loose = read_reply({"body": body}).flags.get("cash", False)
    monkeypatch.setenv("PARLEY_CASH_STRICT", "1")
    assert read_reply({"body": body}).flags.get("cash", False) is strict
    if not strict and "Faster payment is welcome" in body:
        assert loose


def test_cash_strict_a_stock_phrase_does_not_mark_the_supplier_cash_short(case, monkeypatch):
    d = Dossier(case, None)
    d.observe("sup:T1", STOCK)
    assert d.profiles["sup:T1"].cash_short
    monkeypatch.setenv("PARLEY_CASH_STRICT", "1")
    d = Dossier(case, None)
    d.observe("sup:T1", STOCK)
    assert not d.profiles["sup:T1"].cash_short
    d.observe("sup:T1", "Our cash flow is under pressure.")
    assert d.profiles["sup:T1"].cash_short


# ---- PARLEY_PI_CAP --------------------------------------------------------------------------------


def _pi_threads(case, prices, asks, date="2027-04-01"):
    base = {"payment_days": 30, "contract_months": 12, "effective_date": date}
    ev = [supplier_msg("sup:T1", 0, {**base, "unit_price": prices[0]})]
    for i, (ask, price) in enumerate(zip(asks, prices[1:], strict=True), 1):
        ev.append(buyer_msg([{**base, "unit_price": ask}]))
        ev.append(supplier_msg("sup:T1", i, {**base, "unit_price": price}))
    return {"sup:T1": ev}


def test_pi_cap_never_asks_above_todays_price_after_asking_at_or_below_it(case, monkeypatch):
    cur = case["buyer"]["current_unit_price"]
    threads = _pi_threads(case, [52.3, 50.5, 49.8], [47.7, 48.0])
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and max(s["unit_price"] for s in d.sheets) > cur
    monkeypatch.setenv("PARLEY_PI_CAP", "1")
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and max(s["unit_price"] for s in d.sheets) <= cur


def test_pi_cap_a_price_at_or_below_today_asks_for_the_earliest_date(case, monkeypatch):
    threads = _pi_threads(case, [52.3], [])
    early = min(case["term_options"]["effective_date"])
    d = policy(case).decide("sup:T1", threads)
    low = [s for s in d.sheets if s["unit_price"] <= case["buyer"]["current_unit_price"]]
    assert low and any(s["effective_date"] != early for s in low)
    monkeypatch.setenv("PARLEY_PI_CAP", "1")
    d = policy(case).decide("sup:T1", threads)
    low = [s for s in d.sheets if s["unit_price"] <= case["buyer"]["current_unit_price"]]
    assert low and all(s["effective_date"] == early for s in low)


# ---- PARLEY_BUNDLE_PROOF --------------------------------------------------------------------------


def _bundle(case):
    case["type"] = "bundling"
    case["refs"] = ["fc:F-1", "doc:D-1"]
    base = {"payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
    ask = {**base, "unit_price": 44.0}
    body = "Guten Tag,\nplease provide the proof of the demand you plan with us.\nViele Gruesse"
    return {
        "sup:T1": [
            supplier_msg("sup:T1", 0, {**base, "unit_price": 50.0}),
            buyer_msg([ask], refs=["fc:F-1"]),
            supplier_msg("sup:T1", 1, {**base, "unit_price": 49.0}, body=body),
        ]
    }


def _proof_refs(d):
    refs = [r for c in d.claims for r in c.refs]
    refs += [d.lines["volume_ref"]] if d.lines.get("volume_ref") else []
    return [r for r in refs if r.startswith(("fc:", "doc:"))]


def test_bundle_proof_answers_a_data_request_with_exactly_one_id(case, monkeypatch):
    threads = _bundle(case)
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and _proof_refs(d) == []
    monkeypatch.setenv("PARLEY_BUNDLE_PROOF", "1")
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and _proof_refs(d) == ["doc:D-1"]


# ---- PARLEY_QUALITY_ONE ---------------------------------------------------------------------------


def _quality(case):
    case["type"] = "quality"
    pol = policy(case)
    bank = [
        Claim("otd", ["sc:Q1"], "Scorecard Q1 (sc:Q1).", 6, None, "sc", "sup:T1"),
        Claim("ppm", ["qn:N1"], "Quality notification (qn:N1).", 5.5, None, "qn", "sup:T1"),
        Claim("otd", ["sc:Q2"], "Scorecard Q2 (sc:Q2).", 5, None, "sc", "sup:T1"),
    ]
    pol.claim_bank["sup:T1"] = bank
    return pol


def _quality_refs(pol, threads, first):
    pol.model("sup:T1", threads["sup:T1"])
    v = pol.view("sup:T1", threads["sup:T1"])
    return [r for c in pol.pick_claims("sup:T1", v, {"sup:T1": v}, first) for r in c.refs]


def test_quality_one_cites_one_quality_fact_per_mail_and_none_again(case, monkeypatch):
    base = {"payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01", "unit_price": 50.0}
    one = {"sup:T1": [supplier_msg("sup:T1", 0, base)]}
    two = {"sup:T1": one["sup:T1"] + [buyer_msg([base], refs=["sc:Q1"]), supplier_msg("sup:T1", 1, base)]}
    assert len(_quality_refs(_quality(case), one, True)) == 2
    assert _quality_refs(_quality(case), two, False)
    monkeypatch.setenv("PARLEY_QUALITY_ONE", "1")
    assert len(_quality_refs(_quality(case), one, True)) == 1
    assert _quality_refs(_quality(case), two, False) == []


# ---- PARLEY_FREEZE_NARROW -------------------------------------------------------------------------


def _freeze(case, body="Guten Tag,\nour offer.\nViele Gruesse"):
    case["type"] = "bundling"
    case["negotiable"] = ["unit_price", "payment_days", "contract_months"]
    case["fixed_terms"]["effective_date"] = "2026-12-01"
    case["term_options"]["payment_days"] = [14, 30, 60, 90]
    case["term_options"]["contract_months"] = [12, 24, 36]
    base = {"payment_days": 30, "contract_months": 12}
    return {
        "sup:T1": [
            supplier_msg("sup:T1", 0, {**base, "unit_price": 52.0}),
            buyer_msg([{**base, "unit_price": 45.0}]),
            supplier_msg("sup:T1", 1, {**base, "unit_price": 50.0}),
            buyer_msg([{**base, "unit_price": 46.0}]),
            supplier_msg("sup:T1", 2, {**base, "unit_price": 50.0}, body=body),
        ]
    }


def test_freeze_narrow_offers_long_payment_and_contract_once(case, monkeypatch):
    threads = _freeze(case)
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and not any(s["payment_days"] == 90 and s["contract_months"] == 36 for s in d.sheets)
    monkeypatch.setenv("PARLEY_FREEZE_NARROW", "1")
    pol = policy(case)
    d = pol.decide("sup:T1", threads)
    assert d.kind == "send" and len(d.sheets) == 1
    assert d.sheets[0]["payment_days"] == 90 and d.sheets[0]["contract_months"] == 36
    assert d.sheets[0]["unit_price"] == 46.0
    d = pol.decide("sup:T1", threads)
    assert "two equal prices" not in d.reason


def test_freeze_narrow_skips_a_supplier_that_signals_cash(case, monkeypatch):
    monkeypatch.setenv("PARLEY_FREEZE_NARROW", "1")
    threads = _freeze(case, body="Guten Tag,\nour liquidity is tight.\nViele Gruesse")
    d = policy(case).decide("sup:T1", threads)
    assert "two equal prices" not in d.reason
