import math

from conftest import buyer_msg, supplier_msg

from parley import writer
from parley.dossier import Claim, Dossier
from parley.kit.cost import total_cost
from parley.policy import CasePolicy, breakdown_floor
from parley.valuation import SupplierModel, Valuation

STD_T1 = {"volume_per_year": 4000, "ppm_commitment": 650, "chargeback_eur_per_complaint": 0.0, "tooling_eur": 0.0}
OPEN_T1 = {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}


def policy(case, facts=None):
    if facts:
        case["facts"] = facts
    return CasePolicy(case, Dossier(case, None))


def opening(case):
    return {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}


# ---- valuation ---------------------------------------------------------------------------------


def test_cost_matches_the_published_function(case):
    v = Valuation(case)
    terms = {"unit_price": 50.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-02-01"}
    assert v.cost(terms, STD_T1) == total_cost(case, terms, STD_T1)


def test_price_for_cost_is_the_highest_price_within_target(case):
    v = Valuation(case)
    combo = {"payment_days": 45, "contract_months": 24, "effective_date": "2027-04-01"}
    target = 400_000.0
    p = v.price_for_cost(combo, target, STD_T1)
    assert v.cost({**combo, "unit_price": p}, STD_T1) <= target
    assert v.cost({**combo, "unit_price": round(p + 0.01, 2)}, STD_T1) > target


def test_index_clause_is_worth_more_to_the_supplier_than_it_costs_us(clause_case):
    v = Valuation(clause_case)
    m = SupplierModel(standard={"payment_days": 30, "contract_months": 12, "index_clause": None}, interest_rate=0.08)
    clause = clause_case["term_options"]["index_clause"][1]
    fixed = {"unit_price": 5.6, "payment_days": 30, "contract_months": 24, "index_clause": None}
    indexed = {**fixed, "index_clause": clause}
    gain = v.supplier_value(indexed, m) - v.supplier_value(fixed, m)
    our_cost = (v.cost(indexed) - v.cost(fixed)) / (20000 * 2)
    assert gain > our_cost > 0


def test_relationship_cost_makes_cost_non_monotonic(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=4, relationship_eur_per_point=5000)
    v = Valuation(case)
    m = SupplierModel(standard={**STD_T1, "payment_days": 30}, full_cost=48.0)
    combo = {"payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    cheapest = v.cheapest_price(combo, STD_T1, m, 100)
    assert 40 < cheapest < 50
    assert v.cost({**combo, "unit_price": cheapest - 3}, STD_T1, m) > v.cost(
        {**combo, "unit_price": cheapest}, STD_T1, m
    )


# ---- policy invariants -------------------------------------------------------------------------


def test_first_ask_is_below_the_opening_and_valid(case):
    pol = policy(case)
    d = pol.decide("sup:T1", opening(case))
    assert d.kind == "send"
    c_open = total_cost(case, OPEN_T1, STD_T1)
    for s in d.sheets:
        assert set(s) == set(case["negotiable"])
        for f, opts in case["term_options"].items():
            assert s[f] in opts
        assert 0 < s["unit_price"] < OPEN_T1["unit_price"]
        assert total_cost(case, s, STD_T1) < c_open


def test_price_increase_keeps_the_latest_effective_date(case):
    d = policy(case).decide("sup:T1", opening(case))
    assert d.sheets[0]["effective_date"] == "2027-04-01"


def test_first_packages_probe_payment_terms(case):
    d = policy(case).decide("sup:T1", opening(case))
    assert len({s["payment_days"] for s in d.sheets}) >= 2


def test_rfq_sheets_keep_must_haves(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    threads = {
        "sup:A": [
            supplier_msg("sup:A", 0, {"unit_price": 17.1, "payment_days": 30, "lead_time_days": 28}, standard=std)
        ],
        "sup:B": [
            supplier_msg(
                "sup:B",
                0,
                {"unit_price": 16.0, "payment_days": 45, "incoterm": "EXW"},
                standard={**std, "lead_time_days": 42},
            )
        ],
    }
    pol = policy(rfq_case)
    for sid in threads:
        d = pol.decide(sid, threads)
        assert d.kind == "send"
        assert all(s["lead_time_days"] <= 42 for s in d.sheets)


def test_rival_offer_is_cited_to_the_dearer_bidder(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    threads = {
        "sup:A": [
            supplier_msg("sup:A", 0, {"unit_price": 17.1, "payment_days": 30, "lead_time_days": 28}, standard=std)
        ],
        "sup:B": [
            supplier_msg("sup:B", 0, {"unit_price": 16.0, "payment_days": 45, "lead_time_days": 35}, standard=std)
        ],
    }
    d = policy(rfq_case).decide("sup:A", threads)
    assert d.claims and d.claims[0].kind == "rival" and d.claims[0].refs == ["offer:B-0"]


def test_close_never_accepts_worse_than_the_alternative(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    dear = {"unit_price": 25.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {s: [supplier_msg(s, 0, dear, standard=std)] for s in rfq_case["suppliers"]}
    d = policy(rfq_case).close(threads, "test")
    assert d.kind == "walk"


def test_close_skips_offers_that_break_a_must_have(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    threads = {
        "sup:A": [
            supplier_msg(
                "sup:A",
                0,
                {"unit_price": 12.0, "payment_days": 30, "lead_time_days": 49, "incoterm": "EXW"},
                standard=std,
            )
        ],
        "sup:B": [
            supplier_msg(
                "sup:B",
                0,
                {"unit_price": 15.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"},
                standard=std,
            ),
            buyer_msg([{"unit_price": 13.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}]),
            supplier_msg("sup:B", 1, {"unit_price": 14.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}),
        ],
    }
    d = policy(rfq_case).close(threads, "test")
    assert d.kind == "accept" and d.supplier_id == "sup:B"


def test_final_offer_is_not_pushed(case):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    final = {"unit_price": 49.5, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {
        "sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, final, status="final_offer")]
    }
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "done"


def test_final_offer_alternative_is_mirrored_when_cheaper(case):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    final = {"unit_price": 49.9, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    alt = {"unit_price": 49.4, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {
        "sup:T1": opening(case)["sup:T1"]
        + [buyer_msg([ask]), supplier_msg("sup:T1", 1, final, status="final_offer", alternatives=[alt])]
    }
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and d.sheets == [alt] and d.lines.get("mirror")


def test_no_walk_before_the_supplier_has_answered_twice(rfq_case):
    case = {**rfq_case, "suppliers": ["sup:A"]}
    case["buyer"] = {**rfq_case["buyer"]}
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    dear = {"unit_price": 30.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    d = policy(case).decide("sup:A", {"sup:A": [supplier_msg("sup:A", 0, dear, standard=std)]})
    assert d.kind == "send"


def test_floor_estimate_is_capped_by_offers(case):
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].should_cost = 60.0
    threads = opening(case)
    d = pol.decide("sup:T1", threads)
    assert d.kind == "send"
    v = pol.view("sup:T1", threads["sup:T1"])
    assert pol.floor_price("sup:T1", v) <= OPEN_T1["unit_price"]


def test_breakdown_floor_reads_the_supplier_xlsx():
    text = "[Cost breakdown]\nElement | EUR per unit | Share\nmaterial | 5.56 | x\nconversion | 2.81 | x\noverhead and profit | 2.14 | x\nUnit price | =SUM(B2:B5) | x"
    assert math.isclose(breakdown_floor(text), 5.56 + 2.81 + 2.14 / 3, abs_tol=0.01)


# ---- claims ------------------------------------------------------------------------------------


def idx_facts(values):
    return {f"idx:S-1@{m}": {"series": "S-1", "month": m, "value": v} for m, v in values.items()}


def test_rising_index_is_never_cited(case):
    facts = idx_facts({"2025-09": 100.0, "2026-03": 104.0, "2026-09": 108.0})
    d = Dossier({**case, "facts": facts}, None)
    assert d.best_decline("S-1") is None


def test_falling_index_uses_the_steepest_valid_window(case):
    facts = idx_facts({"2025-08": 120.0, "2025-09": 110.0, "2026-03": 104.0, "2026-09": 100.0})
    d = Dossier({**case, "facts": facts}, None)
    best = d.best_decline("S-1")
    assert best["refs"] == ["idx:S-1@2025-08", "idx:S-1@2026-03"]
    assert round(best["change"], 1) == -13.3


def test_ids_count_once_per_case(rfq_case):
    rfq_case["refs"] = ["fc:T-2@2026-09"]
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    threads = {
        s: [supplier_msg(s, 0, {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35}, standard=std)]
        for s in rfq_case["suppliers"]
    }
    pol = policy(rfq_case)
    cited = [r for s in threads for c in pol.decide(s, threads).claims for r in c.refs]
    assert cited.count("fc:T-2@2026-09") == 1


def test_should_cost_above_the_price_is_not_cited(case):
    pol = policy(case)
    claim = Claim("should_cost", ["cost:T-1"], "x", 9, 60.0, "cost", "sup:T1")
    v = pol.view("sup:T1", opening(case)["sup:T1"])
    pol.model("sup:T1", opening(case)["sup:T1"])
    assert not pol.applicable("sup:T1", claim, v)


# ---- writer: numbers only from code ------------------------------------------------------------


class FakeLLM:
    def __init__(self, text):
        self.out = text

    def text(self, *a, **k):
        return self.out


def test_llm_paragraph_with_a_number_is_rejected(case):
    d = policy(case).decide("sup:T1", opening(case))
    msg, used = writer.compose(
        case, d, opening(case)["sup:T1"][0], None, True, FakeLLM("We could pay 30 days earlier.")
    )
    assert not used and "pay 30 days earlier" not in msg["body"]


def test_llm_paragraph_without_numbers_is_used_and_figures_stay(case):
    d = policy(case).decide("sup:T1", opening(case))
    text = "Thank you for your letter and for explaining the pressure on your side."
    msg, used = writer.compose(case, d, opening(case)["sup:T1"][0], None, True, FakeLLM(text))
    assert used and text in msg["body"]
    for s in d.sheets:
        assert f"EUR {s['unit_price']:.2f}" in msg["body"]
    assert [t["terms"] for t in msg["term_sheets"]] == d.sheets


def test_po_jump_pair_is_the_low_of_the_year_against_the_latest(case):
    facts = {
        "po:1": {"supplier": "sup:T1", "part": "part:T-1", "unit_price": 44.0, "date": "2025-06-01"},
        "po:2": {"supplier": "sup:T1", "part": "part:T-1", "unit_price": 45.0, "date": "2026-01-10"},
        "po:3": {"supplier": "sup:T1", "part": "part:T-1", "unit_price": 48.0, "date": "2026-09-01"},
    }
    d = Dossier({**case, "facts": facts}, None)
    (lo_id, _, lo), (hi_id, _, hi) = d._po_jump("sup:T1")
    assert (lo_id, hi_id, lo, hi) == ("po:2", "po:3", 45.0, 48.0)


def test_hedged_material_blocks_its_index(case):
    d = Dossier({**case, "facts": idx_facts({"2025-09": 110.0, "2026-09": 100.0})}, None)
    prof = d.profiles["sup:T1"]
    d.series["CU-NOTATION"] = d.series.pop("S-1")
    d._read_text(prof, "For your information: our copper requirement is covered at fixed prices until March 2027.")
    assert "CU-NOTATION" in prof.hedged_series


def test_health_goal_asks_stay_between_our_cheapest_price_and_the_offer(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=4, relationship_eur_per_point=20000)
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = 49.0
    d = pol.decide("sup:T1", opening(case))
    m = pol.models["sup:T1"]
    assert d.kind == "send"
    for s in d.sheets:
        combo = {k: v for k, v in s.items() if k != "unit_price"}
        assert s["unit_price"] >= pol.val.cheapest_price(combo, STD_T1, m, 500) - 0.02 or s["unit_price"] >= 52.0
        assert pol.val.supplier_value(s, m) < pol.val.supplier_value(OPEN_T1, m)


def test_no_claim_on_complaint_ppm(case):
    facts = {f"qn:{i}": {"supplier": "sup:T1", "part": "part:T-1", "ppm": 5000, "date": f"2026-0{i}-01"} for i in range(1, 4)}
    d = Dossier({**case, "facts": facts}, None)
    assert not [c for c in d.claims("sup:T1") if any(r.startswith("qn:") for r in c.refs)]


def test_floor_is_capped_but_the_full_cost_estimate_is_not(case):
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].should_cost = 60.0
    pol.dossier.profiles["sup:T1"].full_cost = 58.8
    threads = opening(case)
    pol.decide("sup:T1", threads)
    assert pol.models["sup:T1"].full_cost == 58.8
    assert pol.floor_price("sup:T1", pol.view("sup:T1", threads["sup:T1"])) <= case["buyer"]["current_unit_price"] * 1.02


def test_volume_commitment_cites_the_forecast(case):
    case["refs"] = ["fc:T-1@2026-09"]
    d = policy(case).decide("sup:T1", opening(case))
    msg, _ = writer.compose(case, d, opening(case)["sup:T1"][0], None, True)
    assert "fc:T-1@2026-09" in msg["refs"] and "(fc:T-1@2026-09)" in msg["body"]


def test_rfq_anchors_deeper_than_a_single_supplier(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    terms = {"unit_price": 20.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {s: [supplier_msg(s, 0, terms, standard=std)] for s in rfq_case["suppliers"]}
    rfq = policy(rfq_case)
    single = {**rfq_case, "suppliers": ["sup:A"]}
    one = policy(single)
    v_rfq = rfq.view("sup:A", threads["sup:A"])
    v_one = one.view("sup:A", threads["sup:A"])
    assert rfq.anchor_price("sup:A", v_rfq) < one.anchor_price("sup:A", v_one)


def test_main_sheet_keeps_the_needed_lead_time_when_late_costs_matter(rfq_case):
    rfq_case["buyer"]["late_cost_per_day"] = 2000
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100, "lead_time_days": 49}
    threads = {s: [supplier_msg(s, 0, {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 42}, standard=std)]
               for s in rfq_case["suppliers"]}
    d = policy(rfq_case).decide("sup:A", threads)
    assert d.sheets[0]["lead_time_days"] <= rfq_case["buyer"]["need_lead_time_days"]


def test_greeting_uses_the_name_after_the_sign_off():
    msg = {"body": "Guten Tag,\nour offer stands.\nViele Grüße aus der Gießerei\nAndreas Moser\nOwner, Demo Gießerei GmbH"}
    assert writer.greeting(msg) == "Dear Andreas Moser,"


def test_greeting_never_uses_the_sign_off_as_a_name():
    for body in ("Hello,\nthanks.\nKind regards,\nDemo Machining Sp. z o.o.", "Hi\nKind regards,", "Offer below.\nBest regards\nSales team"):
        assert writer.greeting({"body": body}) in ("Dear Sir or Madam,", "Dear Sales team,")


def test_greeting_keeps_names_that_contain_company_letters():
    assert writer.greeting({"body": "Hi,\nok.\nBest regards\nAgnieszka Kowalska\nExport"}) == "Dear Agnieszka Kowalska,"


def test_sunday_release_news_and_letters_are_read(tmp_path, case):
    import json as _json

    from parley.kit.data import DataPackage

    pkg_dir, rel = tmp_path / "package", tmp_path / "release-sunday"
    (pkg_dir).mkdir()
    (rel / "data").mkdir(parents=True)
    (pkg_dir / "facts.json").write_text("{}", encoding="utf-8")
    (rel / "facts.json").write_text(_json.dumps({"news:R-1": {"supplier": "sup:T1"}}), encoding="utf-8")
    (rel / "data" / "news.csv").write_text(
        "id,date,supplier_id,topic,headline,body\nnews:R-1,2026-10-05,sup:T1,free_capacity,T1 has free capacity,x\n",
        encoding="utf-8")
    d = Dossier(case, DataPackage(pkg_dir))
    assert d.profiles["sup:T1"].capacity_free
    assert any(c.refs == ["news:R-1"] for c in d.claims("sup:T1"))


def _round2(case, ask_price, reply_price):
    ask = {"unit_price": ask_price, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    reply = {**ask, "unit_price": reply_price}
    return {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, reply)]}


def test_a_big_supplier_step_gets_a_small_reply(case):
    d = policy(case).decide("sup:T1", _round2(case, 46.0, 48.5))
    assert d.kind == "send" and "small reply" in d.reason


def test_we_hold_with_a_new_fact_when_the_supplier_holds(case):
    case["refs"] = ["fc:T-1@2026-09", "cost:T-1"]
    ask = {"unit_price": 46.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, OPEN_T1)]}
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and "hold" in d.reason and d.claims


def test_opening_without_payment_terms_does_not_crash_a_health_goal(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=4, relationship_eur_per_point=15000)
    opening_terms = {"unit_price": 52.9, "effective_date": "2026-12-01"}
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, opening_terms, standard={"volume_per_year": 4000})]}
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = 47.0
    d = pol.decide("sup:T1", threads)
    assert d.kind == "send"
    threads["sup:T1"] += [buyer_msg(d.sheets), supplier_msg("sup:T1", 1, {**opening_terms, "unit_price": 50.5})]
    assert pol.close(threads, "test").kind == "accept"


def test_rfq_never_closes_early_on_an_open_offer(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    t = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {s: [supplier_msg(s, 0, t, standard=std)] for s in rfq_case["suppliers"]}
    pol = policy(rfq_case)
    pol.decide("sup:A", threads)
    pol.replied_at = {s: 1.0 for s in threads}
    assert not pol.stale_best(threads, done={"sup:A"})
    threads["sup:A"].append(buyer_msg([t]))
    threads["sup:A"].append(supplier_msg("sup:A", 1, {**t, "unit_price": 15.0}, status="final_offer"))
    assert pol.stale_best(threads, done={"sup:A"})


def test_ask_stays_below_a_moving_supplier(case):
    ask = {"unit_price": 49.6, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, {**ask, "unit_price": 49.8})]}
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and d.sheets[0]["unit_price"] < 49.8


def test_after_two_holds_a_cash_short_supplier_gets_the_fast_payment_sheet(case):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    stand = {**ask, "unit_price": 50.0}
    cash = "Guten Tag,\nFaster payment helps us out a lot.\nViele Gruesse"
    threads = {"sup:T1": opening(case)["sup:T1"]}
    for n in (1, 2, 3):
        threads["sup:T1"] += [buyer_msg([ask]), supplier_msg("sup:T1", n, stand, body=cash)]
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and d.sheets[0]["payment_days"] == 14 and "payment" in d.reason


def test_never_the_same_ask_three_times(case):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, {**ask, "unit_price": 50.0}),
                                                    buyer_msg([ask]), supplier_msg("sup:T1", 2, {**ask, "unit_price": 48.0})]}
    d = policy(case).decide("sup:T1", threads)
    assert d.kind == "send" and d.sheets[0]["unit_price"] > 47.0


def test_bundling_anchors_deeper_than_routine(case):
    base = {**case, "type": "renewal"}
    base["buyer"] = {**case["buyer"], "category_goal": "routine", "current_unit_price": None}
    bundle = {**base, "type": "bundling"}
    a = policy(base)
    b = policy(bundle)
    va, vb = a.view("sup:T1", opening(base)["sup:T1"]), b.view("sup:T1", opening(bundle)["sup:T1"])
    assert b.anchor_price("sup:T1", vb) < a.anchor_price("sup:T1", va)


def test_rfq_takes_a_clear_winner_that_is_close(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    a0 = {"unit_price": 18.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    ask = {**a0, "unit_price": 15.0}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, a0, standard=std), buyer_msg([ask]), supplier_msg("sup:A", 1, {**a0, "unit_price": 15.15})],
        "sup:B": [supplier_msg("sup:B", 0, {**a0, "unit_price": 19.0}, standard=std)],
    }
    d = policy(rfq_case).decide("sup:A", threads)
    assert d.kind == "done" and "clearly the best" in d.reason


def test_unused_lever_handles_index_clause_values(clause_case):
    std = {"payment_days": 30, "contract_months": 12, "index_clause": None}
    stand = {"unit_price": 6.2, "payment_days": 30, "contract_months": 12, "index_clause": None}
    ask = {**stand, "unit_price": 5.5, "index_clause": clause_case["term_options"]["index_clause"][1], "contract_months": 24}
    threads = {"sup:C": [supplier_msg("sup:C", 0, stand, standard=std)]}
    for n in (1, 2, 3):
        threads["sup:C"] += [buyer_msg([ask]), supplier_msg("sup:C", n, stand)]
    d = policy(clause_case).decide("sup:C", threads)
    assert d.kind in ("send", "done")


def test_llm_opening_about_history_is_rejected(case):
    d = policy(case).decide("sup:T1", opening(case))
    text = "Thank you for the goodwill you showed us earlier and for your offer to go through your calculation."
    msg, used = writer.compose(case, d, opening(case)["sup:T1"][0], None, True, FakeLLM(text))
    assert not used and "goodwill" not in msg["body"]


def test_thread_events_pick_up_the_offer_after_a_timeout():
    from parley.runner import CaseRunner

    r = CaseRunner.__new__(CaseRunner)
    r.fetch_docs = lambda *m, **k: None
    t = {"offer_id": "offer:A-3", "status": "final_offer", "closed": None,
         "messages": [supplier_msg("sup:A", 2, {"unit_price": 30.5}, status="final_offer")],
         "timeouts": [{"offer_id": "offer:A-3", "status": "final_offer", "terms": {"unit_price": 30.5}, "text": "x"}]}
    ev = r.thread_events(t)
    assert ev[-1]["term_sheets"][0]["term_sheet_id"] == "offer:A-3"


def test_rfq_closes_when_a_final_offer_is_the_best(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    t = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, t, standard=std), buyer_msg([t]), supplier_msg("sup:A", 1, {**t, "unit_price": 14.0}, status="final_offer")],
        "sup:B": [supplier_msg("sup:B", 0, {**t, "unit_price": 17.0}, standard=std)],
    }
    pol = policy(rfq_case)
    for s in threads:
        pol.model(s, threads[s])
    pol.replied_at = {"sup:A": pol.clock()}
    assert not pol.final_is_best(threads)
    pol.replied_at = {"sup:A": pol.clock() - 31}
    assert pol.final_is_best(threads)
    threads["sup:B"].append(buyer_msg([t]))
    threads["sup:B"].append(supplier_msg("sup:B", 1, {**t, "unit_price": 13.5}))
    assert not pol.final_is_best(threads)


def test_rfq_closes_when_the_best_offer_has_no_round_left(rfq_case, monkeypatch):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    t = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    a = [supplier_msg("sup:A", 0, t, standard=std)]
    for i in range(1, 11):
        a += [buyer_msg([t]), supplier_msg("sup:A", i, {**t, "unit_price": 16.0 - 0.1 * i})]
    threads = {"sup:A": a, "sup:B": [supplier_msg("sup:B", 0, {**t, "unit_price": 17.0}, standard=std)]}
    pol = policy(rfq_case)
    for s in threads:
        pol.model(s, threads[s])
    pol.replied_at = {"sup:A": pol.clock() - 31}
    assert pol.view("sup:A", a).moves_left <= 0
    assert pol.final_is_best(threads)
    monkeypatch.setenv("PARLEY_ROUND_GUARD", "0")
    assert not pol.final_is_best(threads)
    monkeypatch.delenv("PARLEY_ROUND_GUARD")
    threads["sup:A"] = a[:-2]
    assert not pol.final_is_best(threads)


def test_rfq_parks_a_bidder_far_behind(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    t = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, t, standard=std), buyer_msg([t]), supplier_msg("sup:A", 1, {**t, "unit_price": 13.0})],
        "sup:B": [supplier_msg("sup:B", 0, {**t, "unit_price": 18.0}, standard=std), buyer_msg([t]), supplier_msg("sup:B", 1, {**t, "unit_price": 17.5})],
    }
    pol = policy(rfq_case)
    d = pol.decide("sup:B", threads)
    assert d.kind == "send" and d.lines.get("park") and d.sheets[0]["unit_price"] == 17.5
    msg, _ = writer.compose(rfq_case, d, threads["sup:B"][-1])
    assert "keep this offer open" in msg["body"] and "place the order" not in msg["body"]
    threads["sup:B"] += [buyer_msg(d.sheets), supplier_msg("sup:B", 2, {**t, "unit_price": 17.5})]
    d = pol.decide("sup:B", threads)
    assert d.kind == "done" and "parked" in d.reason


def test_health_goal_never_asks_more_than_the_standing_offer(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=4, relationship_eur_per_point=200000)
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = 50.0
    d = pol.decide("sup:T1", opening(case))

    assert d.kind == "send"
    for s in d.sheets:
        assert pol.val.supplier_value(s, pol.models["sup:T1"]) < pol.val.supplier_value(OPEN_T1, pol.models["sup:T1"])


def test_model_signals_join_the_rule_flags(case):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([ask]), supplier_msg("sup:T1", 1, {**ask, "unit_price": 50.0})]}
    pol = policy(case)
    pol.extra[("sup:T1", 2)] = {"step_announced": True, "tone": "neutral"}
    d = pol.decide("sup:T1", threads)
    assert "internal step" in d.reason



def test_our_cost_matches_the_arena_including_the_supplier_health_charge():
    import json as _json
    from pathlib import Path as _Path

    from parley.dossier import SHOULD_COST_MARKUP

    fx = _json.loads((_Path(__file__).parent / "fixtures" / "health_charge.json").read_text(encoding="utf-8"))
    case = fx["case"]
    v = Valuation(case)
    m = SupplierModel(standard={"payment_days": 30, "incoterm": "DAP", "lead_time_days": 35},
                      interest_rate=0.065, full_cost=fx["should_cost"] / SHOULD_COST_MARKUP)
    ours = v.cost(fx["terms"], None, m)
    assert abs(ours - fx["c_deal"]) / fx["c_deal"] < 0.01
    assert ours > total_cost(case, fx["terms"]) + 0.5 * fx["cost_breakdown"]["relationship"]
def test_every_quality_package_keeps_the_quality_must_have(case):
    case["type"] = "quality"
    case["negotiable"] = ["unit_price", "ppm_commitment", "chargeback_eur_per_complaint"]
    case["term_options"] = {"ppm_commitment": [3000, 2000, 1500, 1000, 500], "chargeback_eur_per_complaint": [0, 150, 300]}
    case["buyer"]["must_haves"] = [{"field": "ppm_commitment", "op": "<=", "value": 1500}]
    std = {**STD_T1, "ppm_commitment": 2000, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
    pol = policy(case)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, {"unit_price": 52.9}, standard=std)]}
    for n in range(3):
        d = pol.decide("sup:T1", threads)
        if d.kind != "send":
            break
        assert d.sheets and all(pol.val.keeps_must_haves(s, std) for s in d.sheets)
        counter = {**d.sheets[0], "unit_price": round(d.sheets[0]["unit_price"] * 1.05, 2)}
        threads["sup:T1"] += [buyer_msg(d.sheets), supplier_msg("sup:T1", n + 1, counter, standard=std)]


def test_walk_compares_like_with_like(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=8, relationship_eur_per_point=7000)
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 54.0, "payment_days": 30}}
    pol = policy(case)
    sheet = {**OPEN_T1, "unit_price": 50.0}
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    for n, p in enumerate((51.0, 50.0), start=1):
        threads["sup:T1"] += [buyer_msg([{**sheet, "unit_price": 45.0}]), supplier_msg("sup:T1", n, {**OPEN_T1, "unit_price": p}, standard=STD_T1)]
    v = pol.view("sup:T1", threads["sup:T1"])
    assert v.c_plain < pol.val.c_alt
    assert pol.decide("sup:T1", threads).kind != "walk"


def test_never_accept_at_or_above_the_cheapest_opening(case):
    dear = {"unit_price": 53.5, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
    threads = {"sup:T1": opening(case)["sup:T1"] + [buyer_msg([dear]), supplier_msg("sup:T1", 1, dear, status="final_offer")]}
    d = policy(case).close(threads, "test")
    assert d.kind == "walk"


def test_every_sheet_costs_less_than_the_standing_offer_and_the_opening(case, rfq_case, clause_case):
    for c, threads in ((case, opening(case)),):
        pol = policy(c)
        for _ in range(1):
            d = pol.decide("sup:T1", threads)
            c_stand = total_cost(c, OPEN_T1, STD_T1)
            for s in d.sheets:
                assert pol.cost("sup:T1", s, STD_T1) < c_stand


def _with_breakdown(msg, price):
    text = (
        "[Cost breakdown]\nElement | EUR per unit | Share of price\n"
        f"Material | {price * 0.5:.2f} | =B2/B6\nConversion | {price * 0.3:.2f} | =B3/B6\n"
        f"Overhead | {price * 0.15:.2f} | =B4/B6\nMargin | {price * 0.05:.2f} | =B5/B6\nUnit price | =SUM(B2:B5) | =SUM(C2:C5)"
    )
    msg["attachments"].append({"name": "cost-breakdown.xlsx", "text": text})
    return msg


def _health_round2(case):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=8, relationship_eur_per_point=20000)
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = None
    threads = opening(case)
    d = pol.decide("sup:T1", threads)
    reply = _with_breakdown(supplier_msg("sup:T1", 1, {**OPEN_T1, "unit_price": 52.0}, standard=STD_T1), 52.0)
    threads["sup:T1"] += [buyer_msg(d.sheets), reply]
    return pol, threads


def test_breakdown_cost_sets_no_health_full_cost_behind_the_flag(case, monkeypatch):
    monkeypatch.setenv("PARLEY_REL_NO_BREAKDOWN_COST", "1")
    pol, threads = _health_round2(case)
    d = pol.decide("sup:T1", threads)
    assert pol.models["sup:T1"].full_cost is None
    assert pol.floor["sup:T1"] > 0
    assert d.kind == "send" and all(s["unit_price"] < 52.0 for s in d.sheets)


def test_breakdown_cost_sets_the_full_cost_when_switched_off(case, monkeypatch):
    monkeypatch.setenv("PARLEY_REL_NO_BREAKDOWN_COST", "0")
    pol, threads = _health_round2(case)
    pol.decide("sup:T1", threads)
    assert pol.models["sup:T1"].full_cost


def test_plain_accept_compares_like_with_like_but_keeps_the_opening_guard(case, monkeypatch):
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=8, relationship_eur_per_point=20000)
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = 50.0
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    threads["sup:T1"] += [buyer_msg([{**OPEN_T1, "unit_price": 45.0}]),
                          supplier_msg("sup:T1", 1, {**OPEN_T1, "unit_price": 51.0}, standard=STD_T1)]
    pol.decide("sup:T1", threads)
    v = pol.view("sup:T1", threads["sup:T1"])
    views = {"sup:T1": v}
    assert v.c_standing > v.c_plain
    alt = (v.c_plain + v.c_standing) / 2
    monkeypatch.setattr(type(pol.val), "c_alt", property(lambda self: alt))
    monkeypatch.delenv("PARLEY_REL_PLAIN_ACCEPT", raising=False)
    assert not pol.acceptable(v, pol.c_open(views))
    monkeypatch.setenv("PARLEY_REL_PLAIN_ACCEPT", "1")
    assert pol.acceptable(v, max(pol.c_open(views), v.c_standing + 1))
    assert not pol.acceptable(v, v.c_standing)


# ---- profile-field variants (PARLEY_PF_*) -----------------------------------------------------


def _with_fields(case, fields):
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].fields = fields
    return pol


def test_profile_variants_are_off_by_default(case, monkeypatch):
    for n in ("FORK", "FREEZE", "FACTS"):
        monkeypatch.delenv(f"PARLEY_PF_{n}", raising=False)
    base = policy(case).decide("sup:T1", opening(case))
    d = _with_fields(case, {"approval_threshold_pct": 2, "uninformed_owner": True}).decide("sup:T1", opening(case))
    assert d.sheets == base.sheets and "fork" not in d.lines


def test_fork_keeps_the_main_sheet_and_adds_an_equal_cost_sheet(case, monkeypatch):
    monkeypatch.setenv("PARLEY_PF_FORK", "1")
    monkeypatch.delenv("PARLEY_PF_FREEZE", raising=False)
    base = policy(case).decide("sup:T1", opening(case))
    pol = _with_fields(case, {"approval_threshold_pct": 2})
    d = pol.decide("sup:T1", opening(case))
    assert d.sheets[0] == base.sheets[0]
    assert len(d.sheets) == len(base.sheets)
    if "fork" in d.lines:
        k, pct = d.lines["fork"]
        fork = d.sheets[k - 1]
        assert fork["unit_price"] >= OPEN_T1["unit_price"] * (1 - pct / 100) - 1e-9
        assert total_cost(case, fork, STD_T1) <= total_cost(case, d.sheets[0], STD_T1) + 1.0


def test_freeze_fork_needs_the_suppliers_confirmation(case, monkeypatch):
    monkeypatch.setenv("PARLEY_PF_FREEZE", "1")
    monkeypatch.delenv("PARLEY_PF_FORK", raising=False)
    pol = _with_fields(case, {"price_freeze": {"until": "2099-12", "scope": "x"}})
    th = opening(case)
    v = pol.view("sup:T1", th["sup:T1"])
    pol.model("sup:T1", th["sup:T1"])
    assert pol.fork_cut("sup:T1", v) is None
    th["sup:T1"][0]["body"] = "Guten Tag,\nhead office has frozen all price reductions.\nGruesse"
    v = pol.view("sup:T1", th["sup:T1"])
    assert pol.fork_cut("sup:T1", v) == 0.0
    pol.dossier.profiles["sup:T1"].fields = {"price_freeze": {"until": "2001-01", "scope": "x"}}
    assert pol.fork_cut("sup:T1", v) is None


def test_uninformed_owner_gets_three_facts(case, monkeypatch):
    monkeypatch.setenv("PARLEY_PF_FACTS", "1")
    pol = _with_fields(case, {"uninformed_owner": True})
    th = opening(case)
    pol.model("sup:T1", th["sup:T1"])
    v = pol.view("sup:T1", th["sup:T1"])
    pol.claim_bank["sup:T1"] = [
        Claim(k, [f"x:{k}"], f"Fact {k}.", 5.0, None, f"{k}:X-{k}", "sup:T1") for k in ("po", "index", "should_cost", "news")
    ]
    assert len(pol.pick_claims("sup:T1", v, {"sup:T1": v}, first=False)) == 3


def _replies(case, prices, body="Guten Tag,\nour offer.\nGruesse"):
    th = opening(case)["sup:T1"]
    for n, p in enumerate(prices, start=1):
        ask = {**OPEN_T1, "unit_price": 45.0}
        th += [buyer_msg([ask]), supplier_msg("sup:T1", n, {**OPEN_T1, "unit_price": p}, standard=STD_T1, body=body)]
    return {"sup:T1": th}


def test_mover_needs_two_cuts_in_a_row(case):
    pol = policy(case)
    th = _replies(case, (51.0, 50.0))
    pol.model("sup:T1", th["sup:T1"])
    assert pol.mover(pol.view("sup:T1", th["sup:T1"]))
    th = _replies(case, (51.0, 51.0))
    assert not pol.mover(pol.view("sup:T1", th["sup:T1"]))


def test_early_holder_needs_a_corroborated_hold(case):
    pol = policy(case)
    th = _replies(case, (52.9,))
    pol.model("sup:T1", th["sup:T1"])
    assert not pol.early_holder("sup:T1", pol.view("sup:T1", th["sup:T1"]))
    th = _replies(case, (52.9,), body="Guten Tag,\nour prices are frozen until December.\nGruesse")
    assert pol.early_holder("sup:T1", pol.view("sup:T1", th["sup:T1"]))
    th = _replies(case, (52.9, 52.9, 52.9), body="Guten Tag,\nfrozen.\nGruesse")
    assert not pol.early_holder("sup:T1", pol.view("sup:T1", th["sup:T1"]))
