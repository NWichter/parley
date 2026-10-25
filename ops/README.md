# ops

Scripts for the Live Final day. Each `.ps1` (Windows) and `.sh` (macOS/Linux) wrapper runs the Python
script next to it with `uv run --script` (Python 3.12+, `PYTHONUTF8=1`). Keys come only from the
repo-root `.env` (`ARENA_API_KEY`, `ANTHROPIC_API_KEY`) or the environment, are handed to the agent
through its environment and are never printed. Logs go to `ops/logs/<date>/`, the artifacts export
to `ops/artifacts/` (both gitignored).

| Script | What it does |
| --- | --- |
| `preflight.ps1` / `.sh` | Checklist with a PASS/WARN/FAIL table: `.env` keys present (names only), kit `git pull`, `uv sync`, unit tests, `GET /v1/me`, open practice sessions, the final case list (403 before wave 1), model IDs and a 1-token call, clock skew against the arena, disk, power/sleep, network, data package and Sunday release, running practice loops. `--play-one` (Sunday: `--play-one --case case:P01 --with-llm`, as Tacto asks for one practice case on the final machine) plays exactly one sandbox case `case:D01` (outside the quota; `--with-llm` to include the models). `--skip-sync` skips pull, sync and tests |
| `sunday-data.ps1` / `.sh` | After the 08:00 release (run 08:05, `--wait-release 20`): kit pull, `uv sync --locked`, `parley data`, a sha256 manifest of the release (`agent/data/release-sunday.manifest.json`, checked by `final.py`), `parley prepare --force` (`--no-force` keeps the cache, `--no-llm` skips it), the profile-field extractor if the agent has one, tests, a dossier smoke test (`smoke_dossier.py`, three suppliers incl. never-played 70550), then a diff. `--keep-unchanged` keeps the old profiles of suppliers without release letters or news and prints the persona changes; `--release-from <dir>` copies a local release in place (rehearsal with `agent/data/release-sample/release-sunday`). No release: prints `SUBMIT FALLBACK` and exits 2. `--dry-run` only snapshots |
| `rehearsal.ps1` / `.sh` | Free local rehearsal: `arena-sim serve --final-rehearsal` behind `fault_proxy.py`, a sim team, Parley through `final.py` from clean checkouts, a summary and a gate. `--faults`: kill mid-wave 1, a crash loop in wave 2 (must switch to the fallback), 60–120 s reply delays, 429 bursts, Sunday release missing. `--crash-test` one kill only, `--release missing/public/error`, `--port` (proxy on port + 1), `--with-llm`, `--reply-delay <s>` every supplier reply after a median delay (`slow_sim.py`; the wait does not count against the buyer's 60 s) |
| `final.ps1` / `.sh` | The Live Final runner: clean checkouts in `<root>/<rc,fallback,fallback2>` (`--root` or `PARLEY_FINAL_ROOT`) (`--rc` a ref or the submission clone, `--fallback F1R`, `--fallback2 F1`), each with a locked venv; `parley run --set final --no-llm` (`--with-llm`/`--mode` to change); restart 3 s after an exit; 3 exits in 120 s switch to the next build; lock file, refuses to start beside practice loops or open sessions; state in `ops/logs/final-state.json`. `--prepare-only` builds the checkouts |
| `status.ps1` / `.sh` | Watcher view: waves, `seconds_left`, open and not-picked-up cases, journal age, build, restarts, switches. Reads only `GET /v1/cases?set=final`, `GET /v1/sessions?set=final` and local files |
| `reader_check.py` | 08:15 offline reader check: rule reader against the model reader (`ANALYST_MODEL`) over the release texts and the supplier mails of recent ended sessions (read-only GETs, no quota, about $0.03). Run from `agent/`: `uv run --frozen python ../ops/reader_check.py` |

Run the Python ones with `uv run --script ops/<name>.py`.

## Status (Sun 05:35)

Sun 05:00–05:35: `sunday_data.py` end to end against the October sample release (`--release-from`,
6 min, all steps PASS), `reader_check.py`, pre-flight with sandbox D01 (0.990), and on server 001
two `rehearsal.py --faults` runs (fallback F1R and F2: 7/7 gates, pickup 0.4–2.0 s) plus steps S1–S5
of `RUNBOOK.md` with a test submission. The final runs on server 001 (`RUNBOOK.md`).

## Status (Sat 21:40)


## Sunday timeline

| Time (CET) | Step | Command |
| --- | --- | --- |
| before 08:00 | Rehearse once more if the agent changed | `ops/rehearsal.ps1 --crash-test` |
| 08:00 | Sunday data release | `ops/sunday-data.ps1` |
| 08:30 | Pre-flight on the final machine, one practice case as Tacto asks (1 session) | `ops/preflight.ps1 --play-one --case case:P01 --with-llm` |
| 08:30–12:30 | Last agent changes; after each one: tests, a rehearsal | `ops/rehearsal.ps1` |
| 13:00 | Code freeze: submit on Hack&More with the commit hash the final runs | `git rev-parse HEAD` |
| 13:00–13:25 | Stop every practice loop, then pre-flight without syncing | `ops/preflight.ps1 --skip-sync` |
| before 13:30 | Start the final runner and leave it alone (it waits on the 403) | `ops/final.ps1` |
| 13:30–~14:20 | Hands off. Only if it died: start it again, it picks up its sessions | `ops/final.ps1` |
| 15:10 | Top-5 pitch at the Tacto office | `frontend/public/pitch/` |

During the final nothing reads `/v1/events`, `/screen`, `/leaderboard` or `/results`; the status line
comes from the agent's own case list.
