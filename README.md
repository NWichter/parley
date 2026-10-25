# Parley

**Code does the math, AI does the talking: verified facts and equal-cost trades, built on game theory.**

Parley negotiates with industrial suppliers by e-mail, on its own, for the lowest total cost over
the contract, and explains every trade it makes.

Built by Team Clooless at the Industrial Agents Hackathon, Munich, 24–25 October 2026, for the
**Tacto Negotiation Arena** track.

- Live site: https://parley.sorevo.de · demo: https://parley.sorevo.de/demo · tech: https://parley.sorevo.de/tech
- Pitch deck: https://parley.sorevo.de/pitch/ · replays: https://parley.sorevo.de/replay
- Architecture, guards and parameters: [`ARCHITECTURE.md`](ARCHITECTURE.md) · submission text: [`submission.md`](submission.md)
- Artifacts for the judges (Google Drive): https://drive.google.com/drive/folders/1koHEV2coKTKhmZrKV7LnHFTTnfZR87HR?usp=sharing

**In short (our own practice runs, not the Live Final):** the final build (v1.38 with the round guard, no
language model) scored a mean of **0.792** on the ten hosted practice cases, with no zeros, and was
ahead of Tacto's house agent (≈ 0.70) on **7 of 10**. Details under [Results](#results).

## The challenge

Parley buys parts for Hollerau Pumpenwerke, a fictional German pump maker. Each case starts with a
supplier e-mail: a price increase, an expiring contract, a request for quotes. Parley reads the company
data, negotiates with Tacto's supplier agents using term sheets, and then accepts one offer or walks
away. There is no human in the loop. A deal is scored on its total cost over the contract: price,
payment terms, freight, quality, lead time, coverage and the supplier's health.

## How it works

Parley is a deterministic negotiation engine. Code decides every price, term, data ID and figure, and
when to accept or walk; templates write the e-mails around them. The templates pick an opening for the
situation (price letter, renewal, RFQ, capacity, quality) and do not repeat themselves within a thread;
a mail lint in the test suite (`eval/audit_mails.py`) checks every buyer e-mail of a bank run.
The full data flow, the guards G1–G9, the parameters and how we tested them, the arena client and the
fallback chain are in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

1. **Prepare.** Before the first e-mail, Parley builds a dossier per supplier from the data package
   and the case file: every fact it may cite, with its data ID and the figure the arena will check
   (should-cost, notice periods, falling cost-driver indices, scorecards, complaints, forecasts, the
   supplier's own last price jump, press items). IDs are checked against the package's `facts.json`
   before they go into a mail. The Sunday 08:00 release is pulled by `parley data`, checksummed, and
   its news, letters and facts join the dossier the same way (inputs "bought forward" count as
   hedged, a lost customer in the news as free capacity).
2. **Know the supplier.** For each of the 46 detailed suppliers, `agent/cache/profile_fields.json`
   holds 17 structured fields taken only from Tacto's data package: approval threshold and cycle,
   preferred and rejected levers, capacity, price freeze, cash need, hedged inputs, who negotiates.
   Every value carries its source ID and a verbatim quote that code checks against the source: no
   quote, no value. One model extracted them, two other model families cross-checked them
   (`eval/profile_fields/`). They are data, not rules: a field maps to the same behaviour for every
   supplier, and any use of them in the policy goes through the same test gate as every other change.
3. **Value.** Every term sheet is priced with the arena's published cost function (C). For each
   combination of the negotiable terms, Parley also estimates what the package is worth to the
   supplier in cash: its cost of money, the freight it carries, the units sold before a new price,
   the risk an index clause takes off it.
4. **Negotiate.** Round 1 anchors below the opening (deeper for leverage categories and RFQs, a further
   3 % of the opening price lower for relationship goals, never below the supplier's estimated floor) and cites the
   strongest facts. The main sheet is the cheapest
   package for us at the value we offer; up to two more sheets **cost us about the same** and differ in one
   term (payment, index clause, lead time), so the supplier's choice shows what it values. Each later
   round moves a share of the gap: less after a big step from the supplier, almost nothing when it
   announces an internal step, more when it holds. One new fact per round. In RFQs each bidder has its own thread, the first
   moves are decided in a fixed supplier order, the cheapest rival offer goes to every dearer bidder,
   and a clear best bid within 3 % of our ask is taken and closes the case at once.
5. **Close.** A final offer is accepted, or, if the supplier offered an alternative sheet that is
   cheaper for us, that sheet is sent back exactly as offered. Parley accepts the cheapest standing
   offer that keeps every must-have and beats the alternative, walks away when nothing can, and begins
   closing 90 seconds before a wave's cap. **Acceptance checks:** an offer is taken only if it keeps
   every must-have and costs less than the opening and the alternative, and while a deal's estimated
   score is low and a fresh fact or an unused lever is left, Parley plays it instead of closing.
   Nine guards (G1–G9, pinned by tests) fence every move: no counter-offer after a final offer, no
   index clause as a lever toward a hedged supplier, one move ID per send, a plain profile if the
   dossier fails, and more.

Every decision is logged with its reason to a JSONL journal per session.

## Parameters

The constants that shape a negotiation (first-ask depth per goal, should-cost clamp 0.94, reciprocity
0.6, step limits 5–25 % of the gap, two facts in round 1, cost of money per supplier type, the RFQ take
rule, accept gap 0.25 %, low-score guard 0.10, send timeout 180 s, close margin 90 s) are listed with
their values, what they do and how we tested them in
[`ARCHITECTURE.md` → Parameters of the final build](ARCHITECTURE.md#parameters-of-the-final-build).
They were set by hand and then tested on our own persona bank (paired, same cases and seeds) and, where
noted, on sandbox and practice cases. A swept value changed only if it gained at least +0.01 in two
runs, with no persona down by more than 0.02 and no new zero.

## How we tested

All tests are our own: our persona bank, the arena's free sandbox cases and our own sessions on the
hosted practice cases.

| Test | What it shows |
| --- | --- |
| **Persona test bank** (`eval/`) | 9 modelled supplier behaviours × 20 generated held-out cases over real parts and suppliers; local and free. A model of supplier behaviour, not the arena: we use it to compare versions and find failure modes, not to predict scores |
| **Paired version comparisons** | every version on the same cases and seeds. A change goes in only if the bank is not lower, no persona drops by more than 0.02, there are no new zeros and the sandbox is not lower. Example, 900 games: v1 crashed in 64 of 180 games; v1.11 scored 0.604 against 0.334 for the naive agent |
| **LLM-voiced suppliers** | the bank with a model reading Parley's mails and writing the supplier's replies: Claude Sonnet, Opus and Haiku, Gemini 4 Argon, GPT-6.1 Sol, GPT-6 Luna, DeepSeek. Parley's scores hold whichever model family plays the supplier (≈ 0.69–0.71 on the same games) |
| **Sandbox** | the arena's free cases D01/D02, three runs per variant |
| **Live practice** | the ten hosted practice cases, one session each; no model, hybrid and full back to back on the same cases. Final build v1.38 without a model: mean 0.792, no zeros, ahead of Tacto's house agent on 7 of 10; best general automatic run per case, any version, case-tuned runs left out: 0.830 (`experiments/REPORT.md`) |
| **Fault-injection rehearsal** (`ops/rehearsal.py`) | the final simulated locally: runner killed mid-wave, a crash loop (must switch to the fallback build), 60–120 s supplier delays, 429 bursts, Sunday release missing. Every case got a session, none hit the cap, pickup about 4 s after a kill |
| **Overfitting guard** | a test fails if the agent's code names a case, a supplier ID or a supplier name; three practice cases are hold-outs: P08 and P18 were never used for tuning or validation; P02 was one of three practice cases used to calibrate the supplier-health cost estimate (should-cost / 1.02) against the arena's cost, never to tune a policy rule; a rule needs two independent sources |

## Results

Only measured numbers, each with its source. Practice numbers are practice, not the Live Final. They count
general runs only: case-tuned practice sessions (label `casetuned`, branches `practice/case-tuned` and
`practice/records`) are left out of `experiments/REPORT.md`, of the
best-per-case figure and of the `/replay` best runs (`exclude_best` in `backend/app/replays/data/catalog.json`).

| What | Result | Source |
| --- | --- | --- |
| Final build (v1.38 with the round guard, `--no-llm`), all 10 practice cases, one session each | mean **0.792**, no zeros; ahead of Tacto's house agent on 7 of 10 | `experiments/REPORT.md` |
| Tacto's house agent, same 10 cases | mean ≈ **0.70** (0.697) | house score of each of our arena sessions, `experiments/REPORT.md` |
| Best general automatic run per practice case, any version (case-tuned runs left out) | mean **0.830** | `experiments/REPORT.md` |
| Parley v1 (Saturday build, a model wrote one paragraph per mail), all 10 practice cases | mean **0.760** | `experiments/REPORT.md` |
| Persona test bank, 180 games, v1.36 | **0.70**, naive agent 0.34 | `eval/`, `agent/MERGES.md` (merge gate log); not the arena |
| Model layer on vs off, 90 paired bank games | **+0.0001** (95 % CI −0.004 … +0.005) | `eval/`, see the next section |

v1.38 per case: P01 0.997, P02 0.626, P03 0.963, P04 0.874, P05 0.865, P07 0.786, P08 0.681, P10 0.671,
P11 0.794, P18 0.666. P08 and P18 were never used for tuning or validation; P02 helped calibrate the
supplier-health cost estimate but was never used to tune a policy rule. The final build (v1.38 plus the
round guard) repeated these scores on all ten cases. Practice runs are one session per
case, and a deterministic version repeats its score on a rerun, so they show direction, not spread.

## Why the final runs without a language model

Parley has an optional model layer: `--mode hybrid` (a model reads each reply, templates write) and
`--mode full` (a model also writes one paragraph per e-mail, no digits allowed, template on timeout).
We measured it before deciding:

- **Persona bank with LLM-voiced suppliers,** 90 paired games on the same cases and seeds: full model
  layer minus no model = **+0.0001** (95 % CI −0.004 … +0.005). A plain rerun of the same agent
  moves +0.001. No new zeros, no crashes.
- **Sandbox D01/D02, three runs each:** full minus no model = **−0.003**.
- **Live practice, all 10 hosted cases, no model / hybrid / full back to back:** 0.671 / 0.668 /
  0.688. Full mode's +0.018 comes from one case alone (P11); without it, +0.001.
- **Blind e-mail test,** 12 real situations × 5 writer models × 5 blind judges: template mails rate
  about the same as model prose.

None of this passes our bar (+0.02 on bank and sandbox, no new zeros). The model does not move the
score; the numbers do. So the final runs `--no-llm`: a decision takes
0.2 s instead of 7.4 s (p95) with ten cases in parallel, there are no model timeouts, and no mail can
contain a statement the supplier never made.

## Quick start

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the repo root:

```bash
cp .env.example .env                   # put ARENA_API_KEY in it; real environment variables win
cd agent
uv sync
uv run pytest                          # offline; tests that need the data package skip without it
uv run parley data                     # the arena data package (and the Sunday release) into agent/data/
uv run parley me                       # the key works: team and practice quota
uv run parley play case:D01 --no-llm   # one free sandbox case on rules and templates
```

The supplier profiles (`agent/cache/personas.json`, `agent/cache/profile_fields.json`) ship with the
repo. `uv run parley prepare` rebuilds them with a model; it is optional and needs `ANTHROPIC_API_KEY`.

| Variable | What |
| --- | --- |
| `ARENA_API_KEY` | the team's arena key |
| `ANTHROPIC_API_KEY` | optional, only for `--mode hybrid` / `--mode full` and `parley prepare`; the final runs without it |
| `PLAN_MODEL`, `WRITE_MODEL`, `ANALYST_MODEL` | default `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` |
| `ARENA_URL` | default `https://arena.tacto.ai` |
| `ARENA_DATA` | data package folder, default `agent/data/package` |

The persona test bank runs locally once the data package is there: `uv run eval/bank.py run --agent parley`
from the repo root; see `eval/README.md` and `eval/LLM_BANK.md`.

## Run the Live Final

```bash
cd agent
uv run parley run --set final --parallel 10 --no-llm
```

`parley run --set final` polls `GET /v1/cases?set=final` (a 403 until wave 1), plays every case of a
wave in parallel, one worker per supplier thread, closes 90 seconds before the 8-minute cap and stops
after wave 5. Started again after a crash, it picks up its open sessions. After the 08:00 Sunday
release run `uv run parley data` again. Practice cases count against the quota and need
`--allow-practice`. Journals land in `agent/runs/journal/<session>.jsonl`.

For the final we run it under the supervisor in `ops/` from the repo root: `ops/final.ps1` (Windows)
or `ops/final.sh` (macOS/Linux) runs `parley run --set final --no-llm` from clean checkouts, restarts it
after a crash, walks the fallback chain after three crashes in 120 s and logs to `ops/logs/`.
`ops/README.md` has the pre-flight and the Sunday timeline.

## Robustness

`parley run --set final` waits through the 403 before wave 1, plays every case of a wave in parallel
with one worker per supplier thread and, started again after a crash, picks up its running sessions.
The supervisor in `ops/` restarts it after an exit and, after three crashes in 120 seconds, switches
along the fallback chain: release candidate → the same checkout with the recent switches reset → the tagged
builds F1R and F1. Without a data package the final mode stops at once; a failed check of the
Sunday release only warns, so a restart during a wave goes on.

## Repository layout

| Folder | What |
| --- | --- |
| `agent/` | the buyer agent, a Python package (`parley`) with its own `pyproject.toml` |
| `eval/` | the persona test bank, LLM-voiced suppliers, mail lint, profile-field checks |
| `experiments/` | the per-case report of our practice runs (`REPORT.md`), the hypothesis log (`hypotheses.md`) |
| `ops/` | supervisor, pre-flight, fault-injection rehearsal |
| `frontend/`, `backend/` | the demo site and its API |
| `audio/` | replay voicing, voice map and portraits |

Modules of `agent/parley/`:

| Module | What |
| --- | --- |
| `dossier.py` | facts per case and supplier, each with its data ID; supplier profiles; the Sunday release |
| `valuation.py` | C of any term sheet; the supplier's cash value of a package; price solvers |
| `policy.py` | anchor, concession, packages, claims per round, accept / mirror / walk, guards |
| `rules.py` | case-level rules known before the first e-mail (relationship goals: deeper first ask) |
| `pacing.py` | time-aware pacing from measured round trips; acts only when time binds before the cap |
| `analyst.py` | reads a supplier reply: status, claim verdicts, signal sentences (rules; optional model tags) |
| `writer.py` | the e-mail: templates plus figures, IDs and term sheets from code (optional model paragraph) |
| `llm.py` | optional model layer: Anthropic SDK with hard timeouts and token accounting |
| `prepare.py` | one-off supplier profiles by the plan model, cached in `cache/personas.json` |
| `runner.py` | one worker per supplier thread, cap handling, resume after a crash, journal |
| `kit/` | modules vendored from Tacto's starter kit (see `kit/LICENSE`) |

## Demo site

- `frontend/`: Next.js 16, Tailwind CSS 4, shadcn/ui, bun
- `backend/`: FastAPI, Python 3.15, uv (the demo site's API)
- `agent/`: Python 3.12+, httpx, the arena's cost function from the starter kit; the optional model layer uses the Anthropic API
- The frontend proxies `/api/*` to the backend, so one domain serves both.

Run it in two terminals, each from the repo root, after `cp .env.example .env`:

```bash
# terminal 1
cd backend && uv run fastapi dev app/main.py --port 8000
# terminal 2
cd frontend && bun install && bun dev
```

Open http://localhost:3000. API health: http://localhost:3000/api/health. Tests: `cd backend && uv run pytest`.
`docker-compose.yml` builds `web` (port 3000) and `api` (port 8000); the live site runs it on Coolify.
To run both containers locally with published ports:
`docker compose -f docker-compose.yml -f docker-compose.local.yml up --build` → http://localhost:3000.

### API

| Method | Path      | What               |
| ------ | --------- | ------------------ |
| GET    | `/health` | API and LLM status |
| GET    | `/replays` | our ended arena sessions (practice, sandbox, final) with score, house score and voice status |
| GET    | `/replays/{session_id}` | the voiced replay if cached, else `none` / `generating` / `failed` |
| POST   | `/replays/{session_id}` | voice one of our ended sessions (no body); poll the GET until `ready` |
| GET    | `/replays/{session_id}/audio` | the MP3 |

## Replays and voices

`/replay` shows one replay per case: our highest-scoring ended session per case and set (practice,
sandbox, later final), with its score against Tacto's house agent. Other sessions are not listed;
`GET /api/replays?all=1` with the admin token lists them all. Voicing a replay fetches the finished
session (`ARENA_API_KEY`, GET only), trims each e-mail by fixed rules (greetings, signatures and
boilerplate out, everything else kept word for word), casts one voice per party from
`audio/voices.json` and calls ElevenLabs text-to-dialogue with timestamps (`ELEVENLABS_API_KEY`). The
whole session is voiced, every turn; long sessions go out in several requests and are joined into one
MP3. Result and MP3 are cached in the `replays` volume (`/data/replays/<session>/`).

Voicing is not open to visitors. It runs from the CLI (`uv run audio/reenact.py best`, see below) or
through `POST /api/replays/<session>` with the header `X-Admin-Token: $REPLAY_ADMIN_TOKEN` (unset =
off). `REPLAY_VOICE_PUBLIC=true` brings back the public "Voice this replay" button. Always: only ended
sessions the arena key returns (ours), one generation at a time (a single worker queue), no user text.
`REPLAY_SESSION_CHARS` and `REPLAY_DAILY_CHARS` are optional caps; 0 or unset means no limit. Without
the API the page falls back to the replays shipped in `frontend/public/replays/` and the P11 example.
`/replay/journal` is the round-by-round decision journal of P11.

Each supplier contact on the cast cards has a portrait: a flat, faceless SVG bust in the site's
drawing style, built from data by `uv run audio/portraits.py <ids> | --all --data <tacto data dir>`
(no image model). Role sets clothes and prop, company category a small backdrop motif; founding year,
staff size, ownership and ABC class show as small marks (the country is shown beside the card, not in
the picture). Gender comes only from a named contact's first name; unnamed contacts get a
gender-neutral figure, and neckwear and headwear do not depend on gender. Beards go only to contacts
with a male first name (stable hash of the supplier id, about a third). No grey hair or receding
hairlines, so no invented age. Hair, glasses, headwear, neckwear, prop variant, pose and colours are
seeded from supplier id and contact name; a small override table separates suppliers that would look
alike at card size. Every run checks
that no two portraits share a feature signature and that their perceptual hashes (rendered thumbnails,
256 bits) differ by at least `--min-hamming` bits (default 56); a portrait that comes too close is
redrawn. Output goes to `frontend/public/replays/portraits/<id>.svg`. Cards without a portrait show the contact's initials.

The voice map (`audio/voices.json`) is fictional replay casting for the 46 full-detail suppliers and
the sandbox demo suppliers. A contact name and role appear only where the arena's own mails sign with
a name (gender then follows the first name); every other contact is a neutral "Supplier
representative" with a voice picked by hash. Style is the same for all ("clear, professional,
conversational"), with no accent and nothing derived from country or company size; bidders in one RFQ
always get different voices. Parley is Daniel, the narrator Alice. Rebuild it and the case catalog and
journal reasons with `uv run audio/reenact.py assets`, or only the voice map, offline, with
`uv run audio/reenact.py voices --data <tacto data dir>`; voice our best session per case with
`uv run audio/reenact.py best [--set practice] [--dry-run]` (the dry run prints the characters and
requests each would need), or any sessions with
`uv run audio/reenact.py batch <session ids> | --set practice --top 6 [--budget CHARS] [--dry-run]`.

## Pitch deck

Static HTML in `frontend/public/pitch/`. Arrow keys or click to navigate, `?slide=N` shows one slide,
`?print` shows all. The run is 5:00 (ten slides, ends at 4:50; after slide 5 come
the P02 failure and fix and the P05 decision log), P11 is a backup after the close. The close slide's QR
code (`pitch/img/qr-site.svg`) points to `deck.site` in `data.js`. Figures, slide order, the supplier slide, the team and the speaker script all
come from `pitch/data.js`, which the deck and the pitch notes both read. `render.sh` exports PNGs, a PDF and `og.png` with headless Edge. Fonts: Barlow,
Barlow Condensed and JetBrains Mono (SIL Open Font License), served from `pitch/fonts/`.

## Licences and data

Parley's own code: copyright 2026 Team Clooless. `agent/parley/kit/` and `eval/kit/` are vendored from
Tacto's starter kit and keep its notice (`LICENSE` in each). Files derived from the data package (`agent/cache/personas.json`,
`agent/cache/profile_fields.json`, the test-bank cases and personas in `eval/`) build on the data of
the Tacto Negotiation Arena, Tacto Technology GmbH, CC BY 4.0.
