# Parley agent

The buyer agent for the Tacto Negotiation Arena. Code owns every number: prices, terms, data IDs,
figures, when to accept or walk. A language model only writes one paragraph per e-mail and tags the
supplier's replies; without a key, or with `--no-llm`, templates and rules do everything.

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd agent
uv sync
uv run parley data          # data package into agent/data/package (and data/release-sunday once public)
uv run parley prepare       # supplier profiles with the plan model, cached in cache/personas.json
```

`parley prepare` rebuilds the profiles by itself when the data changes (the cache records which data it
was built from). After the Sunday release: `uv run parley data`, then `uv run parley prepare`.

## Environment

Read from the repo-root `.env` (real environment variables win):

| Variable | What |
| --- | --- |
| `ARENA_URL` | default `https://arena.tacto.ai`; a local `arena-sim` for plumbing tests |
| `ARENA_API_KEY` | the team's arena key |
| `ARENA_DATA` | data package folder (default `agent/data/package`, else the kit's `data/`) |
| `ANTHROPIC_API_KEY` | optional; without it Parley runs on templates |
| `PLAN_MODEL`, `WRITE_MODEL`, `ANALYST_MODEL` | default `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` |
| `PARLEY_JOURNAL_DIR` | where the journals go (default `agent/runs/journal`) |
| `PARLEY_SAFE_CLOSE` | `1`: a failing decision is retried once, then the anchor sheet goes out; no close in round 0 while time is left (default off) |
| `PARLEY_WALK_CMIN` | `1`: relationship goals walk on the minimum of C over prices, and close when the offer is at that minimum (default off) |

## Run

```bash
uv run parley me                                   # team and practice quota
uv run parley play case:D01                        # one case; --no-llm for rules and templates only
uv run parley run --set sandbox                    # both sandbox cases in parallel
uv run parley run --set practice --allow-practice --cases case:P01 case:P04
uv run parley run --set final --parallel 10 --no-llm   # the Live Final: waits through the 403, plays each wave
uv run parley dossier case:D01 --set sandbox       # what Parley knows before the first e-mail
uv run pytest                                      # invariants, overfitting guard
```

Modes: `--no-llm` (rules and templates only; the final default), `--mode hybrid` (a model reads each
supplier reply and its signals join the rule flags; templates write), `--mode full` (the model also
writes one paragraph per e-mail). In every mode code sets every number, data ID and term sheet. Without
a data package `run --set final` stops at once; a failed check of the Sunday release only warns, so a
restart during a wave goes on.

Practice cases count against the team quota, so `play` and `run` refuse them without
`--allow-practice`, and `run --set practice` needs an explicit `--cases` list.

In the final, `run --set final` polls the case list, plays every case of a wave in parallel (one worker
per supplier thread), closes 90 seconds before the wave's cap, and stops after the fifth wave. Started
again after a crash, it picks up its running sessions (409) and goes on.

Pacing follows the clock, not only the round limit. Each thread measures its round trips (decision plus
the supplier's reply; a thread without its own yet borrows those of the other threads and cases) and
estimates a mean and a p90. From the time left until the close it counts the rounds still feasible: the
next one must fit its p90, every later one the mean. When that is fewer than the rounds the limit allows,
Parley compresses: bigger steps (at least 1/(n+1) of the gap), more of the strongest remaining facts per
e-mail, and on the last feasible round one closing package. A message whose reply would land after the
close is not sent; the case then closes on the best valid standing offer that beats the alternative, so
no thread is left open at the cap. `parley play` reads a cap from `PARLEY_DEADLINE` (epoch seconds), as
the persona bank sets it.

## Journal

Every decision lands in `runs/journal/<session>.jsonl` (simulator sessions are prefixed with the
simulator's host): the dossier, each e-mail with the reason for its terms, the cost C of every sheet,
the facts cited, the supplier's reply and how it was read, the close, the result, and the model's token
use for that case.

## Modules

| Module | What |
| --- | --- |
| `dossier.py` | facts per case and supplier with their data IDs; supplier profiles; the Sunday release |
| `valuation.py` | C of any term sheet; what a package is worth to the supplier; price solvers |
| `policy.py` | anchor, reciprocal concessions, packages, facts per round, accept / mirror / walk |
| `analyst.py` | reads a supplier reply (rules; optional model tags) |
| `writer.py` | the e-mail: code writes figures, IDs and term sheets; the model one paragraph |
| `llm.py` | Anthropic SDK with hard timeouts and token accounting per case |
| `prepare.py` | supplier profiles by the plan model, grounded in the source text |
| `runner.py` | sessions, one worker per supplier thread, the wave cap, resume, journal |
| `pacing.py` | round-trip estimates per thread and the rounds still feasible before the cap |
| `kit/` | modules vendored from Tacto's starter kit (MIT, see `kit/LICENSE`) |
