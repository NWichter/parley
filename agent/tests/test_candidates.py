"""Candidate switches (default off): each rule is general and tested on synthetic threads."""

from conftest import buyer_msg, supplier_msg

from parley import writer
from parley.analyst import read_reply
from parley.dossier import Claim, Dossier
from parley.policy import TAPER_STEP, CasePolicy

STD_T1 = {"volume_per_year": 4000, "ppm_commitment": 650, "chargeback_eur_per_complaint": 0.0, "tooling_eur": 0.0}
OPEN_T1 = {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
ASK = {"payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
FLOOR_BODY = "Hello,\nThis is really our bottom line, we cannot go any lower.\nKind regards\nAnna Berger"


def policy(case):
    return CasePolicy(case, Dossier(case, None))


def thread(prices, body="Guten Tag,\nour offer.\nViele Gruesse"):
    """Opening, then (our ask, its reply) pairs."""
    ev = [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]
    for n, (ask, reply) in enumerate(prices, start=1):
        ev += [buyer_msg([{**ASK, "unit_price": ask}]), supplier_msg("sup:T1", n, {**ASK, "unit_price": reply}, body=body)]
    return {"sup:T1": ev}


def quality_case(case):
    case["type"] = "quality"
    case["negotiable"] = ["unit_price", "ppm_commitment", "chargeback_eur_per_complaint"]
    case["term_options"] = {"ppm_commitment": [3000, 2000, 1000, 500], "chargeback_eur_per_complaint": [0, 150, 300]}
    case["buyer"]["cost_per_defect"] = 120
    case["buyer"]["category_goal"] = "quality_first"
    return case


def step_of(pol, threads, d):
    v = pol.view("sup:T1", threads["sup:T1"])
    return pol.value("sup:T1", d.sheets[0]) - pol.value("sup:T1", v.asks[-1][0]["terms"])


# ---- PARLEY_TAPER ------------------------------------------------------------------------------


def test_taper_concedes_in_small_fixed_steps_after_round_two(case, monkeypatch):
    threads = thread([(46.0, 51.5), (46.8, 49.5)])
    monkeypatch.delenv("PARLEY_TAPER", raising=False)
    pol = policy(case)
    d_off = pol.decide("sup:T1", threads)
    monkeypatch.setenv("PARLEY_TAPER", "1")
    pol_on = policy(case)
    d_on = pol_on.decide("sup:T1", threads)
    assert d_off.kind == d_on.kind == "send"
    open_value = pol_on.value("sup:T1", OPEN_T1)
    off, on = step_of(pol, threads, d_off), step_of(pol_on, threads, d_on)
    assert off > TAPER_STEP * open_value + 0.05
    assert 0 < on <= TAPER_STEP * open_value + 0.02
    assert "tapered" in d_on.reason


def test_taper_leaves_the_second_round_alone(case, monkeypatch):
    threads = thread([(46.0, 51.0)])
    monkeypatch.setenv("PARLEY_TAPER", "1")
    d = policy(case).decide("sup:T1", threads)
    assert "tapered" not in d.reason


# ---- PARLEY_COMMIT_LEVER -----------------------------------------------------------------------


def test_commit_lever_replaces_the_price_step_at_the_floor(case, monkeypatch):
    threads = thread([(46.0, 50.0), (46.5, 50.0)], body=FLOOR_BODY)
    monkeypatch.setenv("PARLEY_COMMIT_LEVER", "1")
    pol = policy(case)
    d = pol.decide("sup:T1", threads)
    assert d.kind == "send" and d.lines.get("commit") and "call-off" in d.reason
    assert abs(step_of(pol, threads, d)) < 0.02
    msg, _ = writer.compose(case, d, threads["sup:T1"][-1], read_reply(threads["sup:T1"][-1]), False)
    assert writer.COMMIT_LINE in msg["body"]
    threads["sup:T1"] += [buyer_msg(d.sheets), supplier_msg("sup:T1", 3, {**ASK, "unit_price": 50.0}, body=FLOOR_BODY)]
    assert not pol.decide("sup:T1", threads).lines.get("commit")


def test_commit_lever_needs_floor_talk_and_is_off_by_default(case, monkeypatch):
    held = thread([(46.0, 50.0), (46.5, 50.0)], body=FLOOR_BODY)
    monkeypatch.delenv("PARLEY_COMMIT_LEVER", raising=False)
    assert not policy(case).decide("sup:T1", held).lines.get("commit")
    monkeypatch.setenv("PARLEY_COMMIT_LEVER", "1")
    plain = thread([(46.0, 50.0), (46.5, 50.0)])
    assert not policy(case).decide("sup:T1", plain).lines.get("commit")


def test_commit_lever_needs_a_volume_term_or_forecast(case, monkeypatch):
    monkeypatch.setenv("PARLEY_COMMIT_LEVER", "1")
    case = quality_case(case)
    pol = policy(case)
    assert not pol.commit_supported("sup:T1")
    case["refs"] = ["fc:T-1@2026-09"]
    assert policy(case).commit_supported("sup:T1")


# ---- PARLEY_QUALITY_GUARD ----------------------------------------------------------------------

Q_STD = {"volume_per_year": 4000, "ppm_commitment": 1000, "chargeback_eur_per_complaint": 150.0,
         "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
Q_OPEN = {"unit_price": 52.9, "ppm_commitment": 1000, "chargeback_eur_per_complaint": 150.0}


def _quality_sheets(case):
    pol = policy(quality_case(case))
    d = pol.decide("sup:T1", {"sup:T1": [supplier_msg("sup:T1", 0, Q_OPEN, standard=Q_STD)]})
    return d.sheets


def _looser(s, ref):
    return s["ppm_commitment"] > ref["ppm_commitment"] or s["chargeback_eur_per_complaint"] < ref["chargeback_eur_per_complaint"]


def test_quality_guard_never_offers_looser_quality_terms(case, monkeypatch):
    monkeypatch.delenv("PARLEY_QUALITY_GUARD", raising=False)
    off = _quality_sheets(dict(case, buyer=dict(case["buyer"])))
    assert any(_looser(s, Q_OPEN) for s in off)
    monkeypatch.setenv("PARLEY_QUALITY_GUARD", "1")
    on = _quality_sheets(case)
    assert on and not any(_looser(s, Q_OPEN) for s in on)
    assert not any(_looser(s, on[0]) for s in on[1:])


def test_quality_guard_keeps_a_looser_sheet_that_is_cheaper_at_the_floor(case, monkeypatch):
    monkeypatch.setenv("PARLEY_QUALITY_GUARD", "1")
    case = quality_case(case)
    case["buyer"]["cost_per_defect"] = 0
    pol = policy(case)
    ev = [supplier_msg("sup:T1", 0, Q_OPEN, standard=Q_STD)]
    pol.model("sup:T1", ev)
    v = pol.view("sup:T1", ev)
    pol.floor["sup:T1"] = 45.0
    loose = {"unit_price": 47.0, "ppm_commitment": 2000, "chargeback_eur_per_complaint": 150.0}
    assert pol.loosens_quality("sup:T1", v, loose, Q_OPEN)
    pol.cost = lambda sid, t, std=None: 1.0 if t.get("ppm_commitment") == 2000 else 2.0
    assert not pol.loosens_quality("sup:T1", v, loose, Q_OPEN)


# ---- PARLEY_INDEX_R1 ---------------------------------------------------------------------------


def _bank():
    steel = Claim("index", ["idx:S-1@2025-01", "idx:S-1@2026-01"], "Steel fell.", 7.5, -4.3, "idx:S-1", "sup:T1")
    energy = Claim("index", ["idx:E-1@2025-02", "idx:E-1@2026-02"], "Power fell.", 6.2, -17.1, "idx:E-1", "sup:T1")
    rising = Claim("index", ["idx:E-2@2025-02", "idx:E-2@2026-02"], "Gas rose.", 6.9, 3.0, "idx:E-2", "sup:T1")
    otd = Claim("otd", ["sc:T1@2026Q3"], "Late deliveries.", 7.0, 70.0, "sc", "sup:T1")
    return [steel, otd, rising, energy]


def _first_claims(case, monkeypatch, on):
    if on:
        monkeypatch.setenv("PARLEY_INDEX_R1", "1")
    else:
        monkeypatch.delenv("PARLEY_INDEX_R1", raising=False)
    pol = policy(case)
    pol.claim_bank["sup:T1"] = _bank()
    names = {"S-1": "Producer prices: basic iron and steel", "E-1": "Producer prices: electric power", "E-2": "Natural gas"}
    pol.dossier.series_name = lambda s: names.get(s, s)
    return [c.topic for c in pol.decide("sup:T1", {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}).claims]


def test_index_r1_cites_the_falling_energy_index_in_round_one(case, monkeypatch):
    assert "idx:E-1" not in _first_claims(case, monkeypatch, on=False)
    topics = _first_claims(case, monkeypatch, on=True)
    assert "idx:E-1" in topics and "idx:E-2" not in topics and "sc" in topics


# ---- PARLEY_ACK_OPENING (from the P11 comparison) ----------------------------------------------


def test_ack_opening_names_the_suppliers_concern_without_figures(case, monkeypatch):
    body = ("Hello,\nWe cannot give up margin right now. Faster payment would help us more than anything.\n"
            "Our price follows our main cost drivers: LCI-C, PPI-C241.\nKind regards\nMarek Lis")
    threads = thread([(46.0, 50.0)], body=body)
    last = threads["sup:T1"][-1]
    d = policy(case).decide("sup:T1", threads)
    monkeypatch.setenv("PARLEY_ACK_OPENING", "0")
    plain, _ = writer.compose(case, d, last, read_reply(last), False)
    monkeypatch.setenv("PARLEY_ACK_OPENING", "1")
    msg, _ = writer.compose(case, d, last, read_reply(last), False)
    opening = writer.opening_of(msg["body"])
    assert "faster payment matters to you" in opening and "margin" in opening
    assert "faster payment matters" not in plain["body"]
    assert not any(ch.isdigit() for ch in opening)
    assert [t["terms"] for t in msg["term_sheets"]] == [t["terms"] for t in plain["term_sheets"]]


def test_ack_opening_says_payment_is_fixed_when_it_is_not_negotiable(case, monkeypatch):
    monkeypatch.setenv("PARLEY_ACK_OPENING", "1")
    case = quality_case(case)
    from parley.analyst import Reading

    text = writer.acknowledgement(case, Reading(status="open", flags={"cash": True}), {"body": "x"})
    assert "fixed for this part" in text


def test_ack_opening_does_not_thank_for_cost_drivers_or_a_refused_breakdown(case):
    from parley.analyst import Reading

    r = Reading(status="open", flags={"breakdown_refused": True})
    r.series_named = ["PPI-C241"]
    assert writer.acknowledgement(case, r, {"body": "Our price follows PPI-C241."}) == ""
