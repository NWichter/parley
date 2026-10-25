# Parley: submission

Team Clooless, Tacto Negotiation Arena track, Industrial Agents Hackathon Munich, 24–25 October 2026.

Code does the math, AI does the talking: verified facts and equal-cost trades, built on game theory

- Repo: https://github.com/NWichter/parley (the agent is `agent/`, how to run it: `README.md`)
- Commit the Live Final runs: the head of `main` in this repository (hash in the submission form)
- Demo video: https://parley.sorevo.de/demo · technical video: https://parley.sorevo.de/tech
- Live site: https://parley.sorevo.de · pitch deck: https://parley.sorevo.de/pitch/ · replays: https://parley.sorevo.de/replay
- Artifacts for the judges (Google Drive): https://drive.google.com/drive/folders/1koHEV2coKTKhmZrKV7LnHFTTnfZR87HR?usp=sharing
- Code: copyright 2026 Team Clooless; Tacto's starter-kit modules keep their notice, data derived
  from the arena's data package is CC BY 4.0 (Tacto Technology GmbH)

## Problem

Every price increase, contract renewal or request for quotes reaches a buyer as a supplier e-mail. To
answer it well, a buyer has to weigh years of orders, invoices, scorecards, market indices and supplier
news, and then trade price against payment terms, freight, quality and lead time. In the Tacto
Negotiation Arena, Parley buys parts for a fictional German pump maker against supplier agents that
check every argument against the same data. A deal is scored on its total cost over the contract.

## Solution: what we built

Parley is a deterministic negotiation engine: **code owns every number** (prices, terms, data IDs,
the figures it cites, when to accept or walk) and templates write the e-mails, with an opening chosen
for the situation and a mail lint over every e-mail of our test runs. The Live Final runs
with `--no-llm`, because our ablation showed that the language-model layer does not move the score.

- **Prepare.** Before the first e-mail Parley builds a dossier per supplier from the data package,
  the Sunday release and the case file: each citable fact with its data ID and the value the arena
  checks (should-cost, notice periods, cost-driver indices, scorecards, complaints, forecasts, the
  supplier's last price jump, press items). IDs are checked against the package's `facts.json` before
  they are cited.
- **Know the supplier.** 17 structured profile fields per supplier (approval threshold, preferred and
  rejected levers, capacity, price freeze, cash need, hedged inputs, who negotiates), extracted only
  from Tacto's data package. Every value has a source ID and a verbatim quote that code checks; one
  model extracted, two other model families cross-checked. A field maps to the same behaviour for
  every supplier.
- **Value.** Every term sheet is priced with the arena's published cost function C. For each mix of
  the negotiable terms Parley also estimates the cash value of the package to the supplier (cost of
  money, freight, units sold before a new price, risk taken off by an index clause).
- **Offer.** Round 1 anchors below the opening, deeper for leverage categories and RFQs, never below
  the estimated supplier floor, and cites the strongest facts. Next to the main sheet it sends up to
  two sheets that **cost us about the same** and differ in one term, so the supplier's choice shows what it
  values. Later rounds move a share of the gap: less after a big step by the supplier, almost nothing
  after an announced internal step, more when it holds. In RFQs the cheapest rival offer goes to each
  dearer bidder, in separate threads.
- **Close or walk.** Parley accepts the cheapest standing offer that keeps every must-have and beats
  the alternative, sends back a cheaper alternative sheet exactly as offered, walks away when nothing
  beats the alternative, and closes 90 seconds before the wave cap. Zero guards: it never accepts at
  or above the opening cost, offer and alternative are compared on the same cost footing, and while a deal would score below 0.10 and a
  fresh fact or an unused lever is left, Parley plays it instead of closing.
- **Robustness.** One worker per supplier thread; a restarted runner picks up its running sessions;
  a supervisor restarts it and switches to a fallback build after three crashes in 120 s.

Every decision is logged with its reason in a JSONL journal per session.

## How we tested

- **Persona test bank** (`eval/`): 9 modelled supplier behaviours × 20 generated held-out cases,
  local and free. Every change is a paired comparison on the same cases and seeds; it goes in only if
  the bank is not lower, no persona drops by more than 0.02, there are no new zeros and the sandbox is
  not lower.
- **LLM-voiced suppliers:** the same bank with a model reading our mails and writing the supplier's
  replies, from several model families (Claude, Gemini, GPT, DeepSeek).
- **Sandbox** D01/D02 three times per variant, and **live practice** on the ten hosted cases.
- **Fault-injection rehearsal** of the final: runner killed mid-wave, crash loop, 60–120 s supplier
  delays, 429 bursts, missing Sunday release.
- **Overfitting guard:** a test fails if the code names a case, supplier ID or supplier name; three
  practice cases are hold-outs (P08 and P18 never used for tuning or validation; P02 was one of three cases
  used to calibrate the supplier-health cost estimate, never to tune a policy rule); a rule needs two
  independent sources.

## Results

Only measured numbers, each with its source. Practice numbers are practice, not the final.

| What | Result | Source |
| --- | --- | --- |
| Parley v1, all 10 practice cases, one session each | mean **0.760**, 10/10 deals | arena practice, `experiments/REPORT.md` |
| Parley v1.38 final build without a model (`--no-llm`, as in the final), all 10 practice cases, one session each | mean **0.792**, no zeros; ahead of the house agent on 7 of 10 (P01, P03, P04, P05, P07, P11, P18) | arena practice, `experiments/REPORT.md` |
| Tacto's house agent, same 10 cases | mean ≈ **0.70** (0.697) | house score of each of our arena sessions, `experiments/REPORT.md` |
| Starter-kit naive agent on P01 | 0.597 (Parley v1: 0.829) | arena practice |
| Model layer on vs off, persona bank with LLM-voiced suppliers, 90 paired games | **+0.0001** (95 % CI −0.004 … +0.005) | `eval/`, our own simulator |
| Model layer on vs off, sandbox D01/D02, 3× each | **−0.003** | arena sandbox |
| No model / hybrid / full, all 10 practice cases back to back | 0.671 / 0.668 / 0.688 (full's gain from one case; without it +0.001) | arena practice, `experiments/REPORT.md` |
| Decision time per message, p95 | 0.2 s without the model, 7.4 s with it | agent journals |
| Persona bank (health-aware score), v1.36, 180 games | **0.70** (gate run 0.704; rerun 0.698, 2 zero games), naive agent 0.339 (36 zero games) | `eval/`, `agent/MERGES.md` (merge gate log), not the arena |
| Persona bank, suppliers voiced by different model families | ≈ 0.69–0.71 whichever model plays the supplier | `eval/LLM_BANK.md`, not the arena |
| Paired version comparison, 900 games | v1.11 0.604, naive 0.334; v1 crashed in 64 of 180 games | `eval/`, not the arena |

v1.38 per case: P01 0.997, P02 0.626, P03 0.963, P04 0.874, P05 0.865, P07 0.786, P08 0.681, P10 0.671,
P11 0.794, P18 0.666 (P08 and P18 were never used for tuning or validation; P02 helped calibrate the supplier-health cost estimate, never a policy rule). Each session ran on a commit whose
decisions on that thread are identical to v1.38 (checked offline). From v1.22 (0.721) to v1.36 the
changes that our own bank and practice runs confirmed: a cash-rate model that keeps the long-payment
offer (P04 +0.058), an RFQ step that names the best rival bid and takes a clear best bid within 3 % of
our ask (P05 0.659 → 0.865), an opening that acknowledges the concern the supplier raised (P11 +0.084),
and a 180 s send timeout so a slow reply is not sent twice.

Practice runs are one session per case, and the arena replays a supplier's reply when our e-mail is
unchanged, so a rerun of a deterministic version repeats its score; they show direction, not spread.

**Best practice score per case (our runs):** 0.830, the mean over the ten practice cases of the best
general automatic run on each case, any version (`experiments/REPORT.md`); case-tuned practice runs are
left out, so is the one run closed by hand.

## How to run

Python 3.12+ and uv. `cp .env.example .env`, add `ARENA_API_KEY`, then in `agent/`: `uv sync`,
`uv run pytest` (offline), `uv run parley data`, `uv run parley play case:D01 --no-llm` for one sandbox
case and `uv run parley run --set final --parallel 10 --no-llm` for the Live Final. The parameters of the
final build and how we tested them are in `ARCHITECTURE.md`; the full quick start is in `README.md`.

## Hack&More form

Final texts for the form fields; character counts measured on the text between the markers.

**Project name** (37 / 60)

```text
Parley – the AI buyer that negotiates
```

**One-line pitch** (99 / 140)

```text
Code does the math, AI does the talking: verified facts and equal-cost trades, built on game theory
```

**Technical Solution** (792 / 800, Markdown)

```markdown
**Architecture:** Tacto data package → dossier → valuation (total cost C) → policy with guards → mail templates → arena API. A reader classifies each reply; every decision is journaled with its reason and replayable.

**Innovation:**
- **Equal-cost packages:** up to three term sheets that cost us about the same but differ in one term (payment, index clause, lead time); the supplier's pick shows what it values
- **Checked facts only:** every claim carries a data ID the arena verifies; profile fields need a verbatim source quote
- **Code owns every number:** no model in the final; 90 paired games showed +0.0001, so it stays deterministic (0.2 s p95)

**Own tests:** 180-game persona bank, sandbox, 10 practice cases: mean 0.792, no zeros, ahead of Tacto's house agent on 7 of 10.
```

**Repository:** https://github.com/NWichter/parley (head of `main`)

**Demo video** (≤ 2 min): https://parley.sorevo.de/demo

**Deployment link:** https://parley.sorevo.de

**Artifacts** (Google Drive): https://drive.google.com/drive/folders/1koHEV2coKTKhmZrKV7LnHFTTnfZR87HR?usp=sharing

**Built with** (15 / 15 tags): Python, uv, httpx, pytest, FastAPI, Next.js, TypeScript, Tailwind CSS,
shadcn/ui, Bun, Docker, Coolify, Claude, ElevenLabs, Tacto Negotiation Arena API
