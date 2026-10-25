# Persona test bank

A free, local test of how a buyer agent generalises to suppliers it has not seen. No arena quota, and
by default no LLM tokens (`--supplier-llm` lets a model read and write for the suppliers): the agent plays unchanged against a local arena (`ARENA_URL=http://127.0.0.1:<port>`)
that speaks the arena's API, with a deterministic supplier persona behind every thread.

**This is a model of supplier behaviour, not the arena's bots.** The personas are built from public
replays and notes on how the arena's suppliers react (claim verdicts, final-offer rules, the RFQ
"half the step now" rule, cash-short owners, hedged inputs). Use the bank to compare agents and
versions and to find failure modes. A score here does not predict an arena score.

## What is in it

| File | What |
| --- | --- |
| `bank.py` | CLI: `run`, `serve`, `cases`; runner, report |
| `personas.py` | the supplier personas and their hidden economics |
| `cases.py` | the held-out case generator over the data package |
| `server.py` | the kit's `arena-sim` with a persona behind each supplier thread |
| `supplier_llm.py` | `--supplier-llm`: an LLM reads the buyer's e-mail and writes the supplier's reply |
| `compare.py` | runs side by side: scores, spend, what the LLM reader picked up |
| `kit/` | modules vendored from Tacto's starter kit (MIT, see `kit/LICENSE`); `sim.py` has small, marked hooks |

### Cases

`cases.py` builds 20 held-out cases (`case:E01`–`case:E20`) with the structure of the practice and
sandbox cases: price increase, renewal, RFQ, raw material, quality, capacity, bundling. Each uses a
real part and real suppliers from the data package, so an agent's dossier finds real facts
(scorecards, contracts, should-cost, indices). Volumes, prices, horizon, alternative quote, must-haves
and the negotiable terms are drawn with a seed (`--case-seed`, default 2026). About one case in eight has
an alternative that is cheaper than any deal, where walking away is the best move.

Scores use the kit's `total_cost`, as on the arena: `score = (C_open − C_deal) / (C_open − C_best)`,
clipped to 0..1. No deal counts as the alternative. A broken must-have scores 0. `C_open` is the cheapest
opening at the supplier's standard terms. `C_best` is the cheapest package that any persona in the case
would sign at its hidden floor (or the alternative, if that is cheaper).

On strategic and bottleneck goals (`stabilise`, `secure_supply`) the arena also charges the
supplier's health: EUR for every margin point the deal leaves the supplier below
`sustainable_margin_pct`, priced with the supplier's true full cost. The bank does the same. Each
persona has a true full cost per unit at its standard terms: should-cost / 1.02 (the arena
calibration) where its floor is built on the should-cost, otherwise floor / (1.055 × 1.02), since
such floors sit 3–8 % above should-cost. `C_open`, `C_deal` and `C_best` all carry that charge
(`total_cost(..., supplier=persona.true_model())`), and `C_best` is the cheapest package at any
price from the persona's floor up, because a price above the floor can cost less once the charge
counts. `score` is this health-aware score; `score_plain` (with `c_open_plain`, `c_deal_plain`,
`c_best_plain`) is the old one without the charge. On other goals both are identical
(`eval/tests/test_bank_health.py`).

### Personas

Every persona has a hidden **floor per lever combination**. It values a package with the kit's
`supplier_value` (cash at its own cost of money: payment days, freight, units sold before a new price,
chargebacks), plus its own preferences: index clause on its main material, contract length, volume,
overtime for a shorter lead time (0.3 % of the price per day), a stricter ppm. Its ask is `floor + (1 − progress) × room`. The
personas differ in what moves `progress` and when they stop.

| Key | Behaviour |
| --- | --- |
| `mirror` | concedes in proportion to the buyer's own moves; "You did not move, so neither do we" |
| `holder` | small steps, and only for one new verified fact per round; ignores walk threats |
| `floor_stop` | concedes fast (rounds 1–3), then "This is our floor" as a final offer; a further push ends the talks |
| `short_fuse` | final offer in round 2–3; a lowball (more than one room below its floor, without strong facts) draws a warning, a second one (or one with a walk threat) a walk-away |
| `auction` | rival-sensitive: a verified lower rival quote or offer gets "half the step now", the rest next round |
| `index_lover` | an index clause on its main material is worth far more to it than it costs the buyer; limited concessions on fixed prices |
| `cash_constrained` | high cost of money: pays for 14 days, prices long payment terms up and pushes them back |
| `dup_punisher` | credits one fact per data type once; repeats are `duplicate`, uncited figures are asked for, wrong IDs or figures cost trust |
| `announced_steps` | announces its next step; gives it in full only if the buyer waits (moves less than 5 % of the room), else a third of it |

All personas react to:
- the term sheets (they agree when a sheet meets their ask and counter on the sheet closest to their ask);
- cited data IDs, checked against the data package and the case: unknown IDs and wrong should-cost or
  quote figures come back `contradicted`; repeated claims come back `duplicate`; relevant facts are credited
  ("it holds and we took it into account", "We did not know that - a fair point"); rising indices get
  "That remains";
- payment, lead time, index clause and volume through the floor;
- walk threats and deadlines in the e-mail text.

Every tunable number sits in two dicts at the top of `personas.py`: `COMMON` (economics and fact
strengths for all personas) and `PARAMS` (per persona: final-offer round, steps, thresholds, cost of
money). `--params file.json` merges a file of the same shape over them, for example:
`{"common": {"overtime_per_day": 0.002}, "holder": {"max_claims": 2}}`.

The arena's final-offer rules hold for all of them: after a final offer, a sheet that meets it is agreed,
while a new counter or a message without a sheet ends the thread. The 60-second rule is on (`--no-clock`
switches it off). A game that runs longer than `--cap` seconds (default 480, a wave) ends without a deal;
the agent learns its cap from `PARLEY_DEADLINE` (epoch seconds). `--reply-delay <s>` makes every
supplier reply after a lognormal delay around that median (0.3x..3x, `--reply-sigma`, default 0.45), the
same per game, thread and round for every agent; like on the arena, the wait does not count against the
buyer's 60 seconds. The arena's suppliers took a median 33 s (p90 61–74 s, max 99 s) in practice.

## Run it

From the repo root (needs the data package: `--data`, `ARENA_DATA`, `agent/data/package` or the kit's `data/`):

```bash
uv run eval/bank.py run --agent "uv run --project agent parley play {case} --no-llm" --seeds 1
uv run eval/bank.py run --agent parley                      # the same, as an alias
uv run eval/bank.py run --agent parley --personas holder auction --cases 5 --seeds 3
uv run eval/bank.py run --agent "v2=uv run --project agent parley play {case} --no-llm" --agent parley
uv run eval/bank.py cases                                   # list the held-out cases
uv run eval/bank.py serve --persona short_fuse              # one persona behind every case, to play by hand
```

`--agent` takes a shell command with `{case}` for the case ID, optionally named `label=<command>`. It
can be given more than once. The command gets `ARENA_URL`, `ARENA_API_KEY` (one key per game) and
`ARENA_DATA`. `ANTHROPIC_API_KEY` is blanked unless `--allow-llm` is given. The kit's naive agent always
runs as a baseline (`--no-baseline` turns it off). The default port is 8000. If it is busy, the bank
takes a free port and says so.

Each run writes `eval/results/<timestamp>/`:
- `results.jsonl`: one row per game (score, score_plain, relationship, deal, end reason, rounds, C values,
  terms, failure tags);
- `report.md`: mean score and plain score per persona, split by relationship and other cases, per
  case, outcomes per persona, and a list of failures;
- `transcripts/`: every e-mail of each game with the persona's back-office notes (progress, floor);
- `logs/`: the agent's output per game;
- `cases.json`: the generated cases.

## LLM-voiced suppliers (`--supplier-llm`)

The arena's bots have an LLM sales agent at the desk: it reads the buyer's whole e-mail and writes the
reply, while prices come from cost and capacity data and claim verdicts from templates. `--supplier-llm`
plays the bank the same way (code in `supplier_llm.py`):

- **The persona's code still decides**: floor, ask, counter sheet, status (`open`, `final_offer`,
  `agreed`, `walked_away`) and the claim verdicts. The model never sets a price.
- **A reader** (an LLM) turns the buyer's prose into signals: the claims it makes (with or without a
  data ID), the one argument it finds strongest, walk threats (1 = mentions alternatives, 2 = ultimatum),
  deadlines, tone (insult, condescension, dismissive lowball, appreciation), promises (payment on time,
  binding volume), questions. Code checks every cited ID against the data package. The persona applies
  the signals with bounded effects, all in `COMMON["llm"]` in `personas.py` (per-persona overrides in
  `PARAMS[<persona>]["llm"]`):

  | Signal | Effect on the round's progress step |
  | --- | --- |
  | strongest argument rests on a verified, credited ID | +0.03 rooms |
  | appreciation (owner voices only) | +0.01 |
  | promise to pay on time | +0.01 (+0.03 for `cash_constrained`) |
  | binding volume in words (volume negotiable) | +0.01 |
  | sum of the bonuses | at most +0.05 |
  | irritation this round: insult 2, condescension 1, dismissive lowball 1, ultimatum 1 (0 for `holder`) | step × (1 − 0.25 × min(points, 2)), at most −50 % |
  | accumulated irritation (−1 per calm round) ≥ 5 (3 for `short_fuse`) | walks away |

  The reader's walk threat, deadline, breakdown request and uncited figures join the regex signals, so
  `short_fuse`, `holder`, `mirror`, `auction` and `dup_punisher` react to what the buyer means, not
  only to keywords.
- **A writer** (an LLM) writes the e-mail in the persona's voice: the owner of a family firm or a group's
  key account manager (from the persona and the supplier's `owner_type`), in German-flavoured English.
  The standing terms line, every claim verdict, every alternative sheet and "This is our final offer."
  must appear verbatim (missing ones are put back in); a euro figure that is not in the decision or the
  buyer's sheets rejects the draft, and the template is sent instead.
- The supplier's thinking time does not count against the buyer's 60 seconds. Each call has a hard
  timeout (`--supplier-timeout`, 30 s) and two retries; any failure falls back to code.

Models: `--supplier-model` takes a Claude ID (`claude-sonnet-5-5`, the default; `claude-haiku-5-5` for a
cheap mode; `claude-opus-5-5`, `claude-fable-5-1`) or an OpenRouter ID with a slash
(`google/gemini-4-argon`, `openai/gpt-6.1-sol`, `deepseek/deepseek-v4.1-flash`, ...), so other model
families can play the supplier. `--reader-model` sets the reader apart (default: the same model).
Keys come from the environment or the repo-root `.env`: Claude calls use `ANTHROPIC_API_KEY_1`,
OpenRouter calls `OPENROUTER_API_KEY`; neither reaches the agent's process. The agent keeps its own
`ANTHROPIC_API_KEY` (only with `--allow-llm`).

Spend is booked per game (`supplier_llm` and `agent_usd` in `results.jsonl`, the agent's from its
`llm: {...}` line) and in total (`usage.json`, the report header). Budgets stop new games once spent:
`--supplier-budget` (USD, Anthropic, default 10), `--openrouter-budget` (default 3) and `--agent-budget`
(default 10). Skipped games go to `skipped.jsonl`.

```bash
# Parley in LLM mode and without a model, against Sonnet-voiced suppliers
uv run eval/bank.py run --agent "llm=parley-llm" --agent "nollm=parley" --allow-llm --no-baseline \
    --supplier-llm --supplier-budget 20
# the same against Gemini as the supplier
uv run eval/bank.py run --agent "llm=parley-llm" --allow-llm --no-baseline --cases 8 \
    --supplier-llm --supplier-model google/gemini-4-argon --openrouter-budget 4
# side by side: scores, spend, what the reader picked up, the buyer's greeting lines
uv run eval/compare.py det=eval/results/<ts1> sonnet=eval/results/<ts2> --out eval/results/<ts2>/compare.md
```

Results of the first runs: `LLM_BANK.md`.

Failure tags: `zero_score`, `supplier_walked`, `buyer_walked`, `timeouts`, `cap_timeout`, `agent_exit`,
`must_have_broken`, `accepted_while_open` (accepted while the supplier was still conceding) and
`agreed_on_own_ask` (the supplier said `agreed` to the agent's own sheet, a sign of overpaying).
