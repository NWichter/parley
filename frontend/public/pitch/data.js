// Data for the deck: numbers, slide order, persona and team.
// Read by the deck (index.html via pitch.js) and by /pitch-notes (at build time); the speaker script,
// Q&A and checklists live in frontend/src/app/pitch-notes/notes.json.
// The object below must stay valid JSON: double quotes, no trailing commas, no comments inside.
window.PITCH = {
  "deck": {
    "title": "Parley",
    "team": "Clooless",
    "url": "parley.sorevo.de",
    "rev": "Rev D",
    "site": "https://parley.sorevo.de",
    "qr": "img/qr-site.svg"
  },
  "order": [
    "cover",
    "problem",
    "does",
    "how",
    "supplier",
    "fail",
    "why",
    "results",
    "general",
    "close"
  ],
  "backup": [
    "formula",
    "moment"
  ],
  "numbers": {
    "best_practice": {
      "v": "0.830",
      "label": "Best general automatic run per case, any version, mean of the ten practice cases; a best-of across runs, never the agent's score; notes and Q&A only",
      "say": "zero point eight three",
      "source": "our arena practice log, set practice, our general automatic runs only (case-tuned practice sessions, label casetuned or branches practice/case-tuned and practice/records, left out, as is the one run closed by hand, P01 v1.3): P01 0.997 v1.22, P02 0.755 v1, P03 0.963 v1.22, P04 0.874 v1.3, P05 0.865 v1.38 (int/final 2e02bd1), P07 0.800 v1.31 flag run, P08 0.892 flag run (hold-out, measure only), P10 0.679 v1.3, P11 0.794 v1.17, P18 0.685 flag run (hold-out, measure only); mean 0.8302. experiments/REPORT.md (general runs only) practice total 83.0"
    },
    "mean_v1": {
      "v": "0.760",
      "label": "Mean score, Parley v1 (Saturday, model wrote the paragraphs), ten practice cases, one attempt each; notes and Q&A only",
      "say": "zero point seven six",
      "source": "experiments/REPORT.md, column parley-v1-llm (Sat 24 Oct)"
    },
    "house_mean": {
      "v": "0.70",
      "label": "Tacto house agent, same ten practice cases; notes and Q&A only",
      "say": "about zero point seven",
      "source": "experiments/REPORT.md, column Tacto agent (mean of ten = 0.697)"
    },
    "wins_v1": {
      "v": "7",
      "label": "Practice cases where Parley v1 beats the house agent (of 10); notes and Q&A only",
      "say": "seven of ten",
      "source": "experiments/REPORT.md, parley-v1-llm against Tacto agent per case"
    },
    "wins_final_build": {
      "v": "7",
      "label": "Practice cases where v1.36 beats the house agent (of 10); notes and Q&A only",
      "say": "seven of ten",
      "source": "v1.36 sessions in our arena practice log against the Tacto agent column of experiments/REPORT.md (wins P01, P03, P04, P05, P07, P11, P18; behind on P02, P08, P10)"
    },
    "final_build_practice": {
      "v": "0.792",
      "label": "v1.38 final candidate, no model: equal-weight mean over the ten practice cases, one session each, no zeros",
      "say": "zero point seven nine two",
      "source": "agent/MERGES.md, row v1.38 (final candidate), live record eafcab9 on all ten cases: P01 0.997, P03 0.963, P04 0.874, P05 0.865, P07 0.786, P10 0.671, P11 0.794; hold-outs P02 0.626, P08 0.681, P18 0.666; mean 0.7923"
    },
    "bank": {
      "v": "0.70",
      "label": "Persona test bank, held-out generated cases, no model, v1.36, scored with the supplier-health charge; notes and Q&A only",
      "say": "zero point seven",
      "source": "eval/bank.py persona bank, 180 games (9 personas x 20 cases, case seed 2026), health-aware score: v1.36 gate run 0.704 (agent/MERGES.md); rerun on main c0e31db Sun 25 Oct 07:14: 0.698, 2 zero games of 180"
    },
    "bank_naive": {
      "v": "0.34",
      "label": "Naive agent on the same bank",
      "say": "zero point three four",
      "source": "same 180 bank games, Sun 25 Oct 07:05: kit naive agent 0.339, 36 zero games"
    },
    "llm_delta": {
      "v": "+0.0001",
      "label": "v1.9 test: full model minus no model, 90 paired simulated games (inside the noise)",
      "say": "one ten-thousandth",
      "source": "README.md section “Why the final runs without a language model”: 90 paired games with LLM-voiced suppliers, Parley v1.9 (d61e681): +0.0001, 95 % CI -0.004 to +0.005; sandbox D01/D02 3x: -0.003"
    },
    "llm_pairs": {
      "v": "90",
      "label": "Paired games in the ablation",
      "say": "ninety",
      "source": "same v1.9 ablation: 9 personas x 10 cases, same seeds per variant"
    },
    "p05_open": {
      "v": "€36.00",
      "label": "P05: Dralveck's opening price per unit",
      "say": "thirty-six euros",
      "source": "replay e642aeb72a327558, round 0"
    },
    "p05_r1": {
      "v": "€30.00",
      "label": "P05: Dralveck's price after Parley's first e-mail",
      "say": "thirty",
      "source": "replay e642aeb72a327558, round 1"
    },
    "p05_deal": {
      "v": "€28.64",
      "label": "P05: accepted price per unit (round-2 package 1: 14 days net, 35 days lead time)",
      "say": "twenty-eight sixty-four",
      "source": "replay e642aeb72a327558, round 2 + accept"
    },
    "p05_score": {
      "v": "0.747",
      "label": "P05: score of this session",
      "say": "zero point seven five",
      "source": "replay e642aeb72a327558, result.score"
    },
    "p05_house": {
      "v": "0.555",
      "label": "P05: Tacto house agent on this case",
      "say": "zero point five five",
      "source": "replay e642aeb72a327558, house_score"
    },
    "p05_saving": {
      "v": "€61,381",
      "label": "P05: total cost saved over the 24-month contract (C_open - C_deal)",
      "say": "sixty-one thousand euros",
      "source": "replay e642aeb72a327558: C_open 305,545, C_deal 244,164"
    },
    "p02_zero": {
      "v": "0.000",
      "label": "P02: score of the early build (v1.16, also v1.17) whose cost model had no supplier-health charge",
      "say": "zero",
      "source": "our arena practice log, practice P02: parley-F1-v1.16-A 029552b2c280199e and three v1.17 runs, all 0.000 (C_deal 2,331,983 above C_open 2,282,867)"
    },
    "p02_price": {
      "v": "€340.94",
      "label": "P02: unit price that build accepted (current price €348.32, our should-cost estimate €374.95)",
      "say": "three forty",
      "source": "replay 029552b2c280199e terms; agent/tests/fixtures/health_charge.json"
    },
    "p02_health": {
      "v": "€224,039",
      "label": "P02: supplier-health charge the arena added to that deal's total cost",
      "say": "two hundred twenty-four thousand euros",
      "source": "arena cost breakdown of the v1.16 deal, field relationship 224,038.95 (agent/tests/fixtures/health_charge.json)"
    },
    "p02_match": {
      "v": "1 %",
      "label": "P02: our cost of that deal now matches the arena's within this margin (test pins it)",
      "say": "within one percent",
      "source": "agent/tests/test_engine.py test_our_cost_matches_the_arena_including_the_supplier_health_charge; agent/MERGES.md v1.18 (supplier full cost = should-cost / 1.02)"
    },
    "p02_v122": {
      "v": "0.269",
      "label": "P02: score after the cost fix (v1.22)",
      "say": "zero point two seven",
      "source": "our arena practice log, parley-v1.22-A 0885001f42db6d2e"
    },
    "p02_v130": {
      "v": "0.562",
      "label": "P02: score with v1.30",
      "say": "zero point five six",
      "source": "our arena practice log, parley-v1.30-main-A da71ec8f9b9db3e1 (no index clause to a hedged supplier)"
    },
    "p02_final": {
      "v": "0.626",
      "label": "P02: score of the final build (v1.36 and v1.38 settings); P02 is a policy hold-out but helped calibrate the supplier-health cost",
      "say": "zero point six three",
      "source": "our arena practice log, parley-v1.33-narrowcash-A ebe0c9d87c1f3cc8, decisions match v1.36; v1.38 live record eafcab9 also 0.626 (agent/MERGES.md)"
    },
    "final_seven": {
      "v": "0.850",
      "label": "v1.38: mean of the seven practice cases that were not hold-outs",
      "say": "zero point eight five",
      "source": "same v1.38 live record: (0.997+0.963+0.874+0.865+0.786+0.671+0.794)/7 = 0.8500"
    },
    "final_holdout": {
      "v": "0.658",
      "label": "v1.38: mean of the three earlier-labelled hold-outs (P02, P08, P18); P02 helped calibrate the supplier-health cost",
      "say": "zero point six six",
      "source": "same v1.38 live record: (0.626+0.681+0.666)/3 = 0.6577; agent/MERGES.md v1.18 (P02 used to fit should-cost / 1.02)"
    },
    "llm_t_off": {
      "v": "0.21 s",
      "label": "Decision time without the model, worst 5 % (p95), v1.9 test; not measured on v1.38",
      "say": "a fifth of a second",
      "source": "v1.9 ablation (Sat 24 Oct 20:50), variant A p95; README.md rounds it to 0.2 s"
    },
    "llm_t_on": {
      "v": "7.43 s",
      "label": "Decision time with the full model, worst 5 % (p95), v1.9 test; not measured on v1.38",
      "say": "seven and a half seconds",
      "source": "v1.9 ablation (Sat 24 Oct 20:50), variant B p95; README.md rounds it to 7.4 s"
    },
    "llm_calls": {
      "v": "0",
      "label": "Buyer model calls at runtime in the final (ops/final.py --no-llm)",
      "say": "zero",
      "source": "README.md: the final runs --no-llm"
    },
    "p01_raise": {
      "v": "9.5 %",
      "label": "P01: price increase the foundry asks for",
      "say": "nine point five percent",
      "source": "practice case P01, supplier letter of 11 Sep 2026 (EUR 126.53 to 138.55)"
    },
    "data_indices": {
      "v": "33",
      "label": "Market price indices in the data package",
      "say": "thirty-three",
      "source": "Tacto Negotiation Arena data package"
    },
    "data_suppliers": {
      "v": "447",
      "label": "Suppliers in the data package",
      "say": "four hundred forty-seven",
      "source": "Tacto Negotiation Arena data package"
    },
    "p05_pkg1": {
      "v": "€28.58",
      "label": "P05 round 1, package 1: 14 days net, 35 days lead time",
      "say": "twenty-eight fifty-eight",
      "source": "replay e642aeb72a327558, Parley round-1 e-mail"
    },
    "p05_pkg2": {
      "v": "€28.68",
      "label": "P05 round 1, package 2: 30 days net, 35 days lead time (similar estimated cost)",
      "say": "twenty-eight sixty-eight",
      "source": "replay e642aeb72a327558, Parley round-1 e-mail"
    },
    "p05_pkg3": {
      "v": "€28.41",
      "label": "P05 round 1, package 3: 14 days net, 42 days lead time",
      "say": "twenty-eight forty-one",
      "source": "replay e642aeb72a327558, Parley round-1 e-mail"
    },
    "p05_r2b": {
      "v": "€28.74",
      "label": "P05 round 2, package 2: 30 days net, 35 days lead time",
      "say": "twenty-eight seventy-four",
      "source": "replay e642aeb72a327558, Parley round-2 e-mail"
    },
    "prof_suppliers": {
      "v": "46",
      "label": "Supplier profiles built offline (the suppliers in the hosted cases)",
      "say": "forty-six",
      "source": "ARCHITECTURE.md (supplier profiles); eval/profile_fields/doc_en.md"
    },
    "prof_fields": {
      "v": "17",
      "label": "Profile fields per supplier, each with source ID and verbatim quote",
      "say": "seventeen",
      "source": "ARCHITECTURE.md (supplier profiles)"
    },
    "final_cases": {
      "v": "30",
      "label": "New cases in the final, drawn from the 46 known suppliers",
      "say": "thirty",
      "source": "Tacto challenge briefing for the final; agent/MERGES.md v1.38 checks (final-like wave test, 30/30 one session)"
    }
  },
  "persona": {
    "case": "P05",
    "case_title": "RFQ: pump shaft for the new HX series",
    "supplier": "sup:70210",
    "name": "Andreas Dralveck",
    "role": "Owner",
    "company": "Zerspanung Dralveck GmbH",
    "country": "DE",
    "about": "22-person machining shop, founder-owned; the owner negotiates himself",
    "portrait": "../replays/portraits/70210.svg",
    "replay": "e642aeb72a327558",
    "score": "p05_score",
    "house": "p05_house",
    "facts": [
      {
        "fact": "A rival quotes €31.50 per unit",
        "id": "offer:70240-0",
        "did": "Cites the rival quote with its ID"
      },
      {
        "fact": "Has not tracked his material prices since the 2022 peak",
        "id": "doc:20260908-70210-M1",
        "did": "Cites the index that fell: AS-14404 −8.5 %"
      },
      {
        "fact": "Second shift on hold since spring",
        "id": "doc:20260722-70210-M2",
        "did": "Offers binding volume: 4,200 pcs a year, 24 months"
      },
      {
        "fact": "Complained about overdue invoices",
        "id": "doc:20260504-70210-P",
        "did": "Offers 14- or 30-day payment, priced into total cost"
      }
    ],
    "steps": [
      {
        "label": "Opening",
        "key": "p05_open",
        "note": "quote"
      },
      {
        "label": "Round 1",
        "key": "p05_r1",
        "note": "facts verified"
      },
      {
        "label": "Round 2",
        "key": "p05_deal",
        "note": "agreed"
      }
    ]
  },
  "team": [
    {
      "name": "Saket Veerubhotla",
      "photo": "img/team/saket.jpg"
    },
    {
      "name": "Tanay Tiwari",
      "photo": "img/team/tanay.jpg"
    },
    {
      "name": "Tanmay Narang",
      "photo": "img/team/tanmay.jpg"
    },
    {
      "name": "Niklas Wichter",
      "photo": "img/team/niklas.jpg"
    }
  ]
};
