# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "httpx>=0.27", "openpyxl>=3.1", "pypdf>=5", "anthropic>=0.70"]
# ///
"""Persona test bank: play a buyer agent against modelled supplier personas on held-out cases.

    uv run eval/bank.py run --agent "uv run --project agent parley play {case} --no-llm" --seeds 1
    uv run eval/bank.py run --agent parley --personas mirror holder --cases 5
    uv run eval/bank.py serve --persona holder --seed 0      # one persona behind every case, by hand
    uv run eval/bank.py cases                                # print the generated cases

An agent is a shell command with `{case}` for the case ID. It gets ARENA_URL, ARENA_API_KEY and
ARENA_DATA in its environment (ANTHROPIC_API_KEY is blanked unless --allow-llm). `naive` is the kit's
naive agent, played in-process; it runs as a baseline unless --no-baseline.
"""

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cases import World, generate  # noqa: E402
from kit.cost import RELATIONSHIP_GOALS  # noqa: E402
from personas import PERSONAS  # noqa: E402
from server import BankSim, serve  # noqa: E402

REPO = HERE.parent
ALIASES = {
    "parley": "uv run --project agent parley play {case} --no-llm",
    "parley-llm": "uv run --project agent parley play {case}",
}
SUPPLIER_KEYS = ("ANTHROPIC_API_KEY_1", "OPENROUTER_API_KEY")
DATA_CANDIDATES = (
    REPO / "agent" / "data" / "package",
    REPO / "internal" / "briefing" / "tacto" / "repo" / "data",
)


def data_dir(arg):
    for cand in (arg, os.environ.get("ARENA_DATA"), *DATA_CANDIDATES):
        if cand and (Path(cand) / "facts.json").is_file():
            return Path(cand).resolve()
    sys.exit("no data package: pass --data or set ARENA_DATA (a folder with facts.json and world.sqlite)")


def free_port(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return port
        except (OSError, OverflowError):
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def parse_agents(specs, baseline):
    out = []
    for i, spec in enumerate(specs or []):
        if spec == "naive" or spec in ALIASES:
            out.append((spec, ALIASES.get(spec, spec)))
            continue
        label, sep, cmd = spec.partition("=")
        if sep and label and " " not in label:
            out.append((label, ALIASES.get(cmd, cmd)))
        else:
            out.append((f"agent{i + 1}", spec))
    if baseline and not any(c == "naive" for _, c in out):
        out.insert(0, ("naive", "naive"))
    return out


# -- one game ---------------------------------------------------------------------------------------


def kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        import signal

        os.killpg(p.pid, signal.SIGKILL)
    p.wait()


def play_naive(base_url, key, case_id):
    from kit.client import ArenaClient
    from kit.naive import NaiveAgent
    from kit.play import RemoteTable, play

    return play(RemoteTable(ArenaClient(base_url, key)), case_id, NaiveAgent())


AGENT_USD = re.compile(r'^llm:\s*(\{.*"usd".*\})\s*$', re.M)


def agent_usd(log_path):
    """The agent's own model spend, from Parley's `llm: {...}` line (0 when it prints none)."""
    try:
        m = AGENT_USD.findall(log_path.read_text(encoding="utf-8", errors="replace"))
        return float(json.loads(m[-1]).get("usd") or 0.0) if m else 0.0
    except (OSError, ValueError):
        return 0.0


def run_game(sim, base_url, data, agent, persona, case_id, seed, cap, out_dir, allow_llm):
    label, cmd = agent
    key = uuid.uuid4().hex
    game = f"{label}__{persona}__{case_id[5:]}__s{seed}"
    sim.bind(key, game, persona, seed)
    t0 = time.monotonic()
    exit_code, note = 0, ""
    log_path = out_dir / "logs" / f"{game}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if cmd == "naive":
        box = {}

        def go():
            try:
                box["r"] = play_naive(base_url, key, case_id)
            except Exception as e:  # noqa: BLE001
                box["e"] = repr(e)

        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(cap)
        if t.is_alive():
            exit_code, note = -1, "cap_timeout"
        elif "e" in box:
            exit_code, note = 1, box["e"]
        log_path.write_text(
            json.dumps(box.get("r") or box.get("e"), default=str, indent=1),
            encoding="utf-8",
        )
    else:
        env = {
            **os.environ,
            "ARENA_URL": base_url,
            "ARENA_API_KEY": key,
            "ARENA_DATA": str(data),
            "PARLEY_DEADLINE": f"{time.time() + cap:.1f}",
        }
        if not allow_llm:
            env["ANTHROPIC_API_KEY"] = ""
        for k in SUPPLIER_KEYS:  # the supplier side's keys never reach the agent
            env.pop(k, None)
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("VIRTUAL_ENV", None)
        with log_path.open("w", encoding="utf-8") as log:
            p = subprocess.Popen(cmd.format(case=case_id), shell=True, cwd=REPO, env=env, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=os.name != "nt")  # fmt: skip
            try:
                exit_code = p.wait(timeout=cap)
            except subprocess.TimeoutExpired:
                exit_code, note = -1, "cap_timeout"
                kill_tree(p)
    took = time.monotonic() - t0
    sim.end_open(key, "cap_timeout" if note == "cap_timeout" else "agent_exit")
    row = collect(sim, key, game, label, persona, case_id, seed, exit_code, note, took, out_dir)
    row["agent_usd"] = round(agent_usd(log_path), 4) if cmd != "naive" else 0.0
    if sim.llm:
        row["supplier_llm"] = sim.llm.summary(game)
        row["reader"] = reader_notes(sim, key)
    return row


def reader_notes(sim, key):
    """What the supplier's LLM reader picked up in each buyer e-mail, and what it did to the step."""
    out = []
    with sim.lock:
        for s in sim._mine(key):
            for t in s["trace"]:
                if t.get("reader") or t.get("effect"):
                    out.append(
                        {"round": t["round"], "sup": t["actor"], "reader": t.get("reader"), "effect": t.get("effect")}
                    )
    return out


def collect(sim, key, game, label, persona, case_id, seed, exit_code, note, took, out_dir):
    with sim.lock:
        mine = [s for s in sim._mine(key)]
    row = {"game": game, "agent": label, "persona": persona, "case_id": case_id, "seed": seed,
           "exit_code": exit_code, "seconds": round(took, 1)}  # fmt: skip
    if not mine:
        row.update(
            deal=False,
            score=0.0,
            score_plain=0.0,
            end_reason="no_session",
            failure="no_session",
            note=note,
        )
        return row
    s = mine[-1]
    r = s["result"] or {}
    case = s["case"]
    last_status = {}
    for t in s["trace"]:
        last_status[t["actor"]] = t.get("status")
    sup_walk = [
        k for k, th in s["threads"].items() if th["closed"] == "walked_away" and s["end_reason"] != "walked_away"
    ]
    late = sum(len(th["late"]) for th in s["threads"].values())
    deal_sup = r.get("supplier_id")
    status_at_close = last_status.get(f"sup:{deal_sup.split(':', 1)[1]}") if deal_sup else None
    row.update(
        session=s["id"],
        case_type=case["type"],
        deal=bool(r.get("deal")),
        score=round(r.get("score") or 0.0, 4),
        score_plain=round(r.get("score_plain") or 0.0, 4),
        relationship=case["buyer"].get("category_goal") in RELATIONSHIP_GOALS,
        end_reason=s["end_reason"],
        supplier_id=deal_sup,
        status_at_close=status_at_close,
        rounds=sum(th["round"] for th in s["threads"].values()),
        timeouts=late,
        supplier_walkaways=sup_walk,
        must_haves_ok=r.get("must_haves_ok"),
        c_open=r.get("c_open"),
        c_deal=r.get("c_deal"),
        c_alt=r.get("c_alt"),
        c_best=r.get("c_best"),
        c_open_plain=r.get("c_open_plain"),
        c_deal_plain=r.get("c_deal_plain"),
        c_best_plain=r.get("c_best_plain"),
        terms=r.get("terms"),
        note=note,
    )
    fails = []
    if row["deal"] and row["must_haves_ok"] is False:
        fails.append("must_have_broken")
    if sup_walk:
        fails.append("supplier_walked")
    if s["end_reason"] == "cap_timeout":
        fails.append("cap_timeout")
    if s["end_reason"] == "agent_exit":
        fails.append("agent_exit")
    if late:
        fails.append("timeouts")
    if row["score"] == 0.0:
        fails.append("zero_score")
    if row["deal"] and status_at_close == "open":
        fails.append("accepted_while_open")
    if row["deal"] and status_at_close == "agreed":
        fails.append("agreed_on_own_ask")
    if not row["deal"] and s["end_reason"] == "walked_away":
        fails.append("buyer_walked")
    row["failure"] = ",".join(fails)
    transcript(s, row, out_dir / "transcripts" / f"{game}.md")
    return row


def transcript(s, row, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = [f"# {row['game']}", "", f"session {s['id']}, case {s['case']['case_id']} ({s['case']['type']}): {s['case']['title']}",
           f"result: deal={row['deal']} score={row['score']} (plain {row.get('score_plain')}) end={row['end_reason']} failures={row['failure'] or '-'}",
           f"C_open {row['c_open']}  C_deal {row['c_deal']}  C_alt {row['c_alt']}  C_best {row['c_best']}", ""]  # fmt: skip
    trace = defaultdict(list)
    for t in s["trace"]:
        trace[t["actor"]].append(t)
    for sup, th in s["threads"].items():
        p = th.get("persona")
        out += [
            f"## {sup} ({p.key if p else '?'}): opening {th['opening']}, floor at standard {p.f_std:.2f}"
            if p
            else f"## {sup}",
            "",
        ]
        notes = iter(trace.get(sup, []))
        for m in th["messages"]:
            who = "BUYER" if m.get("from") == "buyer" else f"SUPPLIER [{m.get('status')}]"
            sheets = [x.get("terms") for x in m.get("term_sheets") or []]
            out.append(f"### {who}")
            if m.get("action"):
                out.append(f"action: {json.dumps(m['action'])}")
            if m.get("body"):
                out.append("```\n" + m["body"].strip() + "\n```")
            for sh in sheets:
                out.append(f"- sheet: {json.dumps(sh, default=str)}")
            if m.get("refs"):
                out.append(f"- refs: {', '.join(m['refs'])}")
            if m.get("claim_results"):
                out.append(f"- claim results: {json.dumps(m['claim_results'])}")
            if who.startswith("SUPPLIER") and th["messages"].index(m) > 0:
                t = next(notes, None)
                if t:
                    out.append(f"- back office: {' | '.join(t['reasons'])}")
            out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


# -- the report -----------------------------------------------------------------------------------


def mean(xs):
    return statistics.mean(xs) if xs else float("nan")


def report(rows, agents, meta):
    labels = [a for a, _ in agents]
    by = defaultdict(list)
    for r in rows:
        by[(r["agent"], r["persona"])].append(r)
        by[(r["agent"], r["case_id"])].append(r)
        by[r["agent"]].append(r)
    personas = sorted({r["persona"] for r in rows}, key=list(PERSONAS).index)
    cases = sorted({r["case_id"] for r in rows})
    md = [f"# Persona bank run {meta['stamp']}", "",
          f"{len(rows)} games: {len(personas)} personas x {len(cases)} cases x {meta['seeds']} seed(s) x {len(labels)} agent(s); "
          f"case seed {meta['case_seed']}, wall time {meta['wall_s']:.0f} s.", "",
          "A model of supplier behaviour, not the arena's bots: read differences between agents, not absolute levels.", ""]  # fmt: skip
    llm = meta.get("supplier_llm")
    if llm:
        md += [f"Supplier LLM: writer `{llm['writer']}`, reader `{llm['reader']}`; {llm['calls']} calls, "
               f"{llm['input_tokens']:,} in / {llm['output_tokens']:,} out tokens, ${llm['usd']:.4f} "
               f"(Anthropic ${llm['anthropic_usd']:.4f}, OpenRouter ${llm['openrouter_usd']:.4f}); failed calls "
               f"{llm['failures']}, template fallbacks {llm['fallbacks']}.", ""]  # fmt: skip
    if meta.get("agent_usd"):
        md += [f"Agent model spend: ${meta['agent_usd']:.4f}.", ""]
    def both(g):
        return f"{mean([r['score'] for r in g]):.3f} | {mean([r.get('score_plain', r['score']) for r in g]):.3f}"

    md += [
        "## Mean score per persona",
        "",
        "`score` carries the arena's supplier-health charge (strategic and bottleneck goals, with the "
        "persona's true full cost); `plain` is the score without it, as the bank scored before.",
        "",
        "| persona | " + " | ".join(f"{a} | {a} plain" for a in labels) + " |",
        "|---|" + "---:|---:|" * len(labels),
    ]
    for p in personas:
        md.append(f"| {p} | " + " | ".join(both(by[(a, p)]) for a in labels) + " |")
    md.append("| **all** | " + " | ".join(both(by[a]) for a in labels) + " |")
    md += ["", "## Relationship cases / other cases", "",
           "| cases | games | " + " | ".join(f"{a} | {a} plain" for a in labels) + " |",
           "|---|---:|" + "---:|---:|" * len(labels)]  # fmt: skip
    for name, want in (("relationship (stabilise, secure_supply)", True), ("other", False)):
        g = {a: [r for r in by[a] if bool(r.get("relationship")) == want] for a in labels}
        md.append(f"| {name} | {len(g[labels[0]])} | " + " | ".join(both(g[a]) for a in labels) + " |")
    md += ["", "## Outcomes per persona", ""]
    cols = [
        "deals",
        "zero",
        "sup_walk",
        "buyer_walk",
        "timeouts",
        "cap",
        "acc_open",
        "own_ask",
        "rounds",
    ]
    md += [
        "| agent | persona | " + " | ".join(cols) + " |",
        "|---|---|" + "---:|" * len(cols),
    ]
    for a in labels:
        for p in personas:
            g = by[(a, p)]
            n = len(g)

            def cnt(tag, g=g):
                return sum(tag in (r.get("failure") or "") for r in g)

            vals = [f"{sum(r['deal'] for r in g)}/{n}", cnt("zero_score"), cnt("supplier_walked"), cnt("buyer_walked"),
                    cnt("timeouts"), cnt("cap_timeout") + cnt("agent_exit"), cnt("accepted_while_open"),
                    cnt("agreed_on_own_ask"), f"{mean([r.get('rounds') or 0 for r in g]):.1f}"]  # fmt: skip
            md.append(f"| {a} | {p} | " + " | ".join(str(v) for v in vals) + " |")
    md += [
        "",
        "## Mean score per case",
        "",
        "| case | type | " + " | ".join(labels) + " |",
        "|---|---|" + "---:|" * len(labels),
    ]
    types = {r["case_id"]: r.get("case_type", "") for r in rows}
    for c in cases:
        md.append(
            f"| {c} | {types[c]} | "
            + " | ".join(f"{mean([r['score'] for r in by[(a, c)]]):.3f}" for a in labels)
            + " |"
        )
    md += [
        "",
        "## Failures",
        "",
        "| agent | game | score | end | failures |",
        "|---|---|---:|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["agent"], r["persona"], r["case_id"])):
        if r.get("failure") and any(
            t in r["failure"]
            for t in (
                "zero_score",
                "walked",
                "cap_timeout",
                "agent_exit",
                "must_have",
                "no_session",
            )
        ):
            md.append(
                f"| {r['agent']} | [{r['game']}](transcripts/{r['game']}.md) | {r['score']:.3f} | {r['end_reason']} | {r['failure']} |"
            )
    md += ["", "Columns: zero = score 0; sup_walk = a supplier walked away; buyer_walk = the agent walked away; "
           "cap = bank cap timeout or agent exited mid-case; acc_open = accepted while the supplier was still "
           "conceding (status open); own_ask = the supplier answered `agreed` on the agent's own sheet."]  # fmt: skip
    return "\n".join(md) + "\n"


# -- commands ---------------------------------------------------------------------------------------


def cmd_run(a):
    data = data_dir(a.data)
    world = World.load(data)
    generated = generate(world, a.cases, a.case_seed)
    personas = a.personas or list(PERSONAS)
    unknown = set(personas) - set(PERSONAS)
    if unknown:
        sys.exit(f"unknown persona(s): {', '.join(sorted(unknown))}; known: {', '.join(PERSONAS)}")
    agents = parse_agents(a.agent, not a.no_baseline)
    if not agents:
        sys.exit("no agent: pass --agent")
    port = free_port(a.port)
    if port != a.port:
        print(
            f"port {a.port} is busy: the bank's arena uses port {port} instead",
            flush=True,
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (HERE / "results").mkdir(parents=True, exist_ok=True)
    for n in range(100):  # runs started in the same second get their own folder
        out = HERE / "results" / (stamp if n == 0 else f"{stamp}-{n}")
        try:
            out.mkdir()
            break
        except FileExistsError:
            continue
    stamp = out.name
    sim = BankSim(world, generated, tag=f"bank{stamp[4:].replace('-', '')}", no_clock=a.no_clock,
                  reply_delay=a.reply_delay, reply_sigma=a.reply_sigma)
    if a.supplier_llm:
        from supplier_llm import SupplierLLM, load_key

        keys = {"anthropic": load_key(REPO, SUPPLIER_KEYS[0]), "openrouter": load_key(REPO, SUPPLIER_KEYS[1])}
        sim.llm = SupplierLLM(keys, a.supplier_model, a.reader_model, timeout=a.supplier_timeout,
                              budgets={"anthropic": a.supplier_budget, "openrouter": a.openrouter_budget})  # fmt: skip
        print(f"supplier LLM: writer {sim.llm.model}, reader {sim.llm.reader_model}; budgets "
              f"${a.supplier_budget:.2f} Anthropic ({SUPPLIER_KEYS[0]}), ${a.openrouter_budget:.2f} OpenRouter", flush=True)  # fmt: skip
    agent_spend = [0.0]
    try:
        server = serve(sim, port=port)
    except RuntimeError:  # another run took the port between the check and the bind
        port = free_port(-1)
        print(f"the port was taken meanwhile: the bank's arena uses port {port}", flush=True)
        server = serve(sim, port=port)
    base = f"http://127.0.0.1:{port}"
    (out / "cases.json").write_text(
        json.dumps({k: c for k, (c, _) in generated.items()}, indent=1),
        encoding="utf-8",
    )
    jobs = [(ag, p, c, s) for ag in agents for s in range(a.seeds) for p in personas for c in generated]
    print(f"bank: {len(jobs)} games ({len(agents)} agent(s) x {len(personas)} personas x {len(generated)} cases x "
          f"{a.seeds} seed(s)) against {base}, results in {out}", flush=True)  # fmt: skip
    t0, rows, lock = time.monotonic(), [], threading.Lock()

    def one(job):
        ag, p, c, s = job
        over = []
        if sim.llm and sim.llm.exhausted():
            over.append("supplier budget")
        if agent_spend[0] >= a.agent_budget:
            over.append("agent budget")
        if over:
            row = {"game": f"{ag[0]}__{p}__{c[5:]}__s{s}", "agent": ag[0], "persona": p, "case_id": c, "seed": s,
                   "deal": False, "score": None, "end_reason": "budget_skip", "failure": f"budget_skip ({', '.join(over)})"}  # fmt: skip
        else:
            try:
                row = run_game(sim, base, data, ag, p, c, s, a.cap, out, a.allow_llm)
            except Exception as e:  # noqa: BLE001
                row = {"game": f"{ag[0]}__{p}__{c[5:]}__s{s}", "agent": ag[0], "persona": p, "case_id": c, "seed": s,
                       "deal": False, "score": 0.0, "score_plain": 0.0, "end_reason": "bank_error", "failure": f"bank_error {e!r}"}  # fmt: skip
        with lock:
            agent_spend[0] += row.get("agent_usd") or 0.0
            if row["end_reason"] == "budget_skip":
                (out / "skipped.jsonl").open("a", encoding="utf-8").write(json.dumps(row) + "\n")
                return row
            rows.append(row)
            with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
            if len(rows) % 20 == 0 or len(rows) == len(jobs):
                print(
                    f"  {len(rows)}/{len(jobs)} games, {time.monotonic() - t0:.0f} s",
                    flush=True,
                )
        return row

    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        list(pool.map(one, jobs))
    server.should_exit = True
    meta = {
        "stamp": stamp,
        "seeds": a.seeds,
        "case_seed": a.case_seed,
        "wall_s": time.monotonic() - t0,
    }
    meta["agent_usd"] = round(agent_spend[0], 4)
    if sim.llm:
        meta["supplier_llm"] = {"writer": sim.llm.model, "reader": sim.llm.reader_model, **sim.llm.summary()}
    (out / "usage.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    md = report(rows, agents, meta)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(f"\nspend: agent ${meta['agent_usd']:.4f} (ANTHROPIC_API_KEY)"
          + (f", supplier ${meta['supplier_llm']['anthropic_usd']:.4f} ({SUPPLIER_KEYS[0]}) + "
             f"${meta['supplier_llm']['openrouter_usd']:.4f} (OpenRouter), fallbacks {meta['supplier_llm']['fallbacks']}"
             if sim.llm else ""))  # fmt: skip
    labels = [x for x, _ in agents]
    print("\nmean score per persona:")
    print(f"  {'persona':18}" + "".join(f"{x:>10}" for x in labels))
    for p in personas:
        print(
            f"  {p:18}"
            + "".join(
                f"{mean([r['score'] for r in rows if r['agent'] == x and r['persona'] == p]):10.3f}" for x in labels
            )
        )
    print(f"  {'all':18}" + "".join(f"{mean([r['score'] for r in rows if r['agent'] == x]):10.3f}" for x in labels))
    print("\nscore / score_plain (without the supplier-health charge):")
    for name, want in (("relationship", True), ("other", False), ("all", None)):
        cells = []
        for x in labels:
            g = [r for r in rows if r["agent"] == x and (want is None or bool(r.get("relationship")) == want)]
            cells.append(f"{mean([r['score'] for r in g]):.3f}/{mean([r.get('score_plain', r['score']) for r in g]):.3f}")
        print(f"  {name:18}" + "".join(f"{c:>14}" for c in cells))
    print(f"\nreport: {out / 'report.md'}  ({meta['wall_s']:.0f} s)")


def cmd_serve(a):
    data = data_dir(a.data)
    world = World.load(data)
    generated = generate(world, a.cases, a.case_seed)
    port = free_port(a.port)
    if port != a.port:
        print(f"port {a.port} is busy: using {port}")
    sim = BankSim(world, generated, tag="manual", no_clock=a.no_clock, reply_delay=a.reply_delay,
                  reply_sigma=a.reply_sigma)
    key = a.key or "bank-key"
    sim.bind(key, "manual", a.persona, a.seed)
    server = serve(sim, port=port)
    print(f"bank arena on http://127.0.0.1:{port}  ARENA_API_KEY={key}  persona={a.persona} seed={a.seed}")
    print(f"cases: {', '.join(generated)}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True


def cmd_cases(a):
    world = World.load(data_dir(a.data))
    for cid, (c, h) in generate(world, a.cases, a.case_seed).items():
        b = c["buyer"]
        print(f"{cid} {c['type']:14} {c['parts'][0]} {','.join(c['suppliers'])}  need {b['need_per_year']} x {b['horizon_years']}y"
              f"  current {b['current_unit_price']}  alt {b['alternative']['kind']}  negotiable {c['negotiable'][1:]}"
              f"  must {[m['field'] + m['op'] + str(m['value']) for m in b['must_haves']]}  trap={h['trap']}")  # fmt: skip


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bank", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data",
        help="data package folder (default: ARENA_DATA, agent/data/package, the kit's data)",
    )
    common.add_argument("--cases", type=int, default=20, help="number of held-out cases")
    common.add_argument("--case-seed", type=int, default=2026)
    common.add_argument("--port", type=int, default=8000)
    common.add_argument("--params", help="JSON with persona parameters to merge over personas.PARAMS")
    common.add_argument("--no-clock", action="store_true", help="switch the 60-second rule off")
    common.add_argument("--reply-delay", type=float, default=0.0,
                        help="median seconds a supplier takes to reply (lognormal, 0.3x..3x; default 0 = instant)")
    common.add_argument("--reply-sigma", type=float, default=0.45, help="spread of --reply-delay (lognormal sigma)")
    r = sub.add_parser(
        "run",
        parents=[common],
        help="play every persona x case (x seed) with each agent",
    )
    r.add_argument(
        "--agent",
        action="append",
        help='"<command with {case}>", "label=<command>", "parley" or "naive"',
    )
    r.add_argument("--seeds", type=int, default=1, help="persona seeds per persona x case")
    r.add_argument("--personas", nargs="*", help=f"subset of: {' '.join(PERSONAS)}")
    r.add_argument("--parallel", type=int, default=6)
    r.add_argument(
        "--cap",
        type=float,
        default=480,
        help="seconds per game before the bank ends it (a wave's cap)",
    )
    r.add_argument("--no-baseline", action="store_true", help="do not add the kit's naive agent")
    r.add_argument(
        "--allow-llm",
        action="store_true",
        help="keep ANTHROPIC_API_KEY in the agent's environment",
    )
    r.add_argument("--agent-budget", type=float, default=10.0,
                   help="USD: stop starting games once the agent's own model spend (its `llm:` line) reaches this")  # fmt: skip
    r.add_argument("--supplier-llm", action="store_true",
                   help="the supplier's reader and writer are an LLM; prices and decisions stay in code")  # fmt: skip
    r.add_argument("--supplier-model", default="claude-sonnet-5-5",
                   help="writer model: claude-* (key ANTHROPIC_API_KEY_1) or an OpenRouter ID such as openai/gpt-6.1-sol")  # fmt: skip
    r.add_argument("--reader-model", help="reader model (default: the writer model)")
    r.add_argument("--supplier-budget", type=float, default=10.0, help="USD cap on the supplier's Anthropic calls")
    r.add_argument("--openrouter-budget", type=float, default=3.0, help="USD cap on the supplier's OpenRouter calls")
    r.add_argument("--supplier-timeout", type=float, default=30.0, help="seconds per supplier model call")
    s = sub.add_parser(
        "serve",
        parents=[common],
        help="serve the bank's arena with one persona, to play by hand",
    )
    s.add_argument("--persona", default="mirror", choices=list(PERSONAS))
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--key", help="the API key to accept (default bank-key)")
    sub.add_parser("cases", parents=[common], help="print the generated held-out cases")
    a = ap.parse_args(argv)
    if getattr(a, "params", None):
        from personas import load_params

        load_params(a.params)
    {"run": cmd_run, "serve": cmd_serve, "cases": cmd_cases}[a.cmd](a)


if __name__ == "__main__":
    main()
