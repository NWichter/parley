import copy

import pytest

BASE_CASE = {
    "case_id": "case:T01",
    "type": "price_increase",
    "title": "Test housing: the foundry wants +9 %",
    "trigger_doc": None,
    "parts": ["part:T-1"],
    "suppliers": ["sup:T1"],
    "negotiable": ["unit_price", "payment_days", "contract_months", "effective_date"],
    "fixed_terms": {"lead_time_days": 35, "incoterm": "FCA"},
    "term_options": {
        "payment_days": [14, 30, 45, 60],
        "contract_months": [12, 24],
        "effective_date": ["2026-12-01", "2027-02-01", "2027-04-01"],
    },
    "buyer": {
        "category_goal": "leverage",
        "need_per_year": 4000,
        "horizon_years": 2,
        "current_unit_price": 48.0,
        "fallback_unit_price": 56.0,
        "fallback_risk_pct": 5,
        "interest_rate": 0.08,
        "reference_payment_days": 60,
        "need_lead_time_days": 35,
        "late_cost_per_day": 0,
        "cost_per_defect": 0,
        "freight_eur_per_unit": {"EXW": 1.9, "FCA": 1.2, "DAP": 0.0},
        "index_paths": {},
        "must_haves": [],
        "alternative": {"kind": "none"},
    },
    "round_limit": 10,
    "refs": [],
}

RFQ_CASE = {
    "case_id": "case:T02",
    "type": "rfq",
    "title": "Test shaft: award one of two",
    "trigger_doc": None,
    "parts": ["part:T-2"],
    "suppliers": ["sup:A", "sup:B"],
    "negotiable": ["unit_price", "payment_days", "lead_time_days", "incoterm"],
    "fixed_terms": {"contract_months": 24},
    "term_options": {
        "payment_days": [14, 30, 45, 60],
        "lead_time_days": [21, 28, 35, 42, 49],
        "incoterm": ["EXW", "FCA", "DAP"],
    },
    "buyer": {
        "category_goal": "leverage",
        "need_per_year": 6000,
        "horizon_years": 2,
        "current_unit_price": None,
        "fallback_unit_price": 19.9,
        "fallback_risk_pct": 5,
        "interest_rate": 0.08,
        "reference_payment_days": 60,
        "need_lead_time_days": 35,
        "late_cost_per_day": 150,
        "cost_per_defect": 40,
        "freight_eur_per_unit": {"EXW": 0.6, "FCA": 0.4, "DAP": 0.0},
        "index_paths": {},
        "must_haves": [{"field": "lead_time_days", "op": "<=", "value": 42}],
        "alternative": {
            "kind": "quote",
            "ref": "quote:T-ALT",
            "terms": {"unit_price": 19.9, "incoterm": "DAP", "payment_days": 60, "lead_time_days": 35},
        },
    },
    "round_limit": 10,
    "refs": [],
}

CLAUSE_CASE = {
    "case_id": "case:T03",
    "type": "raw_material",
    "title": "Test bar: alloy surcharge",
    "trigger_doc": None,
    "parts": ["part:T-3"],
    "suppliers": ["sup:C"],
    "negotiable": ["unit_price", "index_clause", "payment_days", "contract_months"],
    "fixed_terms": {
        "lead_time_days": 10,
        "volume_per_year": 20000,
        "effective_date": "2027-01-01",
        "incoterm": "DAP",
        "tooling_eur": 0,
    },
    "term_options": {
        "index_clause": [
            None,
            {"series": "AS-X", "material_share": 0.45, "deadband_pct": 0, "base_month": "2026-08"},
            {"series": "AS-X", "material_share": 0.45, "deadband_pct": 5, "base_month": "2026-08"},
        ],
        "payment_days": [14, 30, 60, 90],
        "contract_months": [12, 24],
    },
    "buyer": {
        "category_goal": "leverage",
        "need_per_year": 20000,
        "horizon_years": 2,
        "current_unit_price": 5.8,
        "fallback_unit_price": 6.1,
        "fallback_risk_pct": 3,
        "index_risk_pct": 1,
        "interest_rate": 0.08,
        "reference_payment_days": 60,
        "need_lead_time_days": 14,
        "late_cost_per_day": 0,
        "cost_per_defect": 0,
        "freight_eur_per_unit": {"EXW": 0.2, "FCA": 0.1, "DAP": 0.0},
        "index_paths": {"AS-X": 0.02},
        "must_haves": [],
        "alternative": {"kind": "none"},
    },
    "round_limit": 10,
    "refs": [],
}


def quote_pdf_text(stated, standard, fixed=None):
    labels = {
        "unit_price": "Unit price (EUR)",
        "payment_days": "Payment terms (days net)",
        "lead_time_days": "Lead time (days)",
        "volume_per_year": "Volume per year (pcs)",
        "contract_months": "Contract term (months)",
        "effective_date": "Price valid from",
        "incoterm": "Incoterm",
        "tooling_eur": "Tooling (EUR)",
        "ppm_commitment": "Quality commitment (ppm)",
        "chargeback_eur_per_complaint": "Chargeback per complaint (EUR)",
        "index_clause": "Index clause",
    }

    def block(d):
        return "\n".join(f"{labels[k]} {'none' if v is None else v}" for k, v in d.items())

    out = ["Offered terms", block(stated), "Our standard terms (apply to everything not stated above)", block(standard)]
    if fixed:
        out += ["Terms fixed by your enquiry", block(fixed)]
    return "\n".join(out)


def supplier_msg(
    sid, n, terms, status="open", standard=None, body="Guten Tag,\nour offer.\nViele Gruesse", alternatives=()
):
    tag = sid.split(":", 1)[1]
    sheets = [{"term_sheet_id": f"offer:{tag}-{n}", "terms": terms}]
    sheets += [{"term_sheet_id": f"offer:{tag}-{n}-a", "terms": t} for t in alternatives]
    msg = {
        "from": f"sales@{tag}",
        "status": status,
        "body": body,
        "term_sheets": sheets,
        "claim_results": [],
        "attachments": [],
    }
    if standard is not None:
        msg["attachments"].append(
            {
                "attachment_id": f"att:{tag}-{n}",
                "name": f"quote-offer-{tag}-{n}.pdf",
                "text": quote_pdf_text({k: v for k, v in terms.items()}, standard),
            }
        )
    return msg


def buyer_msg(sheets, refs=()):
    return {
        "from": "buyer",
        "subject": "Re",
        "body": "x",
        "term_sheets": [{"terms": s} for s in sheets],
        "refs": list(refs),
    }


@pytest.fixture
def case():
    return copy.deepcopy(BASE_CASE)


@pytest.fixture
def rfq_case():
    return copy.deepcopy(RFQ_CASE)


@pytest.fixture
def clause_case():
    return copy.deepcopy(CLAUSE_CASE)
