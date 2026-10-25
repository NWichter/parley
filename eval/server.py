"""The bank's arena: the kit's arena-sim (vendored in kit/sim.py) with a persona behind every
supplier thread. Each team key is bound to one persona and seed, so an agent plays unchanged
against ARENA_URL with its own key."""

import contextlib
import copy
import math
import random
import zlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kit.cost import total_cost
from kit.sim import Sim, create_app
from personas import PERSONAS


class BankSim(Sim):
    def __init__(self, world, generated, tag="bank", no_clock=False, reply_delay=0.0, reply_sigma=0.45):
        super().__init__(
            {cid: c for cid, (c, _) in generated.items()},
            world.facts,
            "bank",
            no_clock=no_clock,
        )
        self.world = world
        self.hidden = {cid: h for cid, (_, h) in generated.items()}
        self.assign = {}  # team key -> (persona key, seed)
        self.variants = {}
        self.prefix = f"{tag}-"
        self.llm = None  # a SupplierLLM in --supplier-llm mode
        self.names = {}
        self._staged = {}
        self.reply_delay, self.reply_sigma = reply_delay, reply_sigma

    def delay_for(self, s, th):
        """Seconds the supplier takes to reply: lognormal around the median `reply_delay`, clipped to
        0.3x..3x; the same game, thread and round get the same delay for every agent."""
        game = (self.names.get(s["team"]) or "").split("__", 1)[-1]
        rng = random.Random(zlib.crc32(f"{game}|{th['key']}|{th['round']}".encode()))
        x = self.reply_delay * math.exp(self.reply_sigma * rng.gauss(0, 1))
        return min(3 * self.reply_delay, max(0.3 * self.reply_delay, x))

    def bind(self, key, name, persona, seed):
        with self.lock:
            self.teams[key] = name
            self.assign[key] = (persona, seed)
            self.names[key] = name

    @contextlib.contextmanager
    def unlocked(self):
        """Let other games run while this one waits for the model (callers hold the lock)."""
        self.lock.release()
        try:
            yield
        finally:
            self.lock.acquire()

    def _llm_call(self, fn, *a):
        from supplier_llm import BudgetExceeded

        try:
            with self.unlocked():
                return fn(*a)
        except BudgetExceeded:
            return None

    def _persona(self, team):
        return self.assign.get(team, ("mirror", 0))

    def case_for(self, team, case_id):
        if case_id not in self.cases:
            return None
        key = (team, case_id)
        if key not in self.variants:
            cls = PERSONAS[self._persona(team)[0]]
            self.variants[key] = cls.adapt(copy.deepcopy(self.cases[case_id]))
        return self.variants[key]

    def cases_for(self, team):
        return [self.case_for(team, c) for c in self.cases]

    def _supplier(self, team, case, sup, i):
        name, seed = self._persona(team)
        key = (team, case["case_id"])
        if i == 0 or key not in self._staged:
            ps = {s: PERSONAS[name](case, self.hidden[case["case_id"]], s, j, self.world, seed)
                  for j, s in enumerate(case["suppliers"])}  # fmt: skip
            texts = {s: p.opening_text() for s, p in ps.items()}
            if self.llm:
                tag = self.names.get(team)

                def write_all():
                    with ThreadPoolExecutor(max_workers=len(ps)) as pool:
                        return dict(zip(ps, pool.map(lambda p: self.llm.write(p, None, None, None, tag, opening=True), ps.values())))

                for s, t in (self._llm_call(write_all) or {}).items():
                    if t:
                        texts[s] = t
            self._staged[key] = (ps, texts)
        ps, texts = self._staged[key]
        p = ps[sup]
        if i == len(case["suppliers"]) - 1:
            self._staged.pop(key, None)
        return (
            {"unit_price": p.opening},
            p.standard,
            {"persona": p, "opening_text": texts[sup]},
        )

    def _answer(self, s, th, msg, sheets):
        p = th["persona"]
        sig = None
        if self.reply_delay > 0:
            th["last_at"] = self.clock() + 1e6
            wait = self.delay_for(s, th)
            with self.unlocked():
                time.sleep(wait)
            th["last_at"] = self.clock()
        if self.llm:
            th["last_at"] = self.clock() + 1e6  # the supplier's thinking time does not count against the buyer
            if p.status not in ("final_offer", "agreed"):
                sig = self._llm_call(self.llm.read, p, msg, sheets, s, self.names.get(s["team"]))
        p.llm_sig = sig
        r = p.respond(th["round"], msg, sheets, s)
        if self.llm:
            text = self._llm_call(self.llm.write, p, r, msg, sig, self.names.get(s["team"]))
            if text:
                r.text = text
            th["last_at"] = self.clock()
        files = list(dict.fromkeys(r.files))
        reply = self._mail(s, th, r.status, r.terms, r.text, files, r.alternatives)
        if r.status == "walked_away":
            th["closed"] = "walked_away"
        reply["claim_results"] = r.claim_results
        s["trace"].append(
            {
                "round": th["round"],
                "actor": f"sup:{th['key']}",
                "persona": p.key,
                "status": r.status,
                "terms": r.terms,
                "reasons": r.notes,
                "progress": round(p.p, 3),
                "floor_here": round(p.floor(r.terms), 2),
                **({"reader": _brief(sig), "effect": p.llm_log[-1] if p.llm_log else None} if self.llm else {}),
            }
        )
        return reply

    def _health(self, th):
        return th["persona"].true_model()

    def _best(self, case, ths, health=False):
        best = min(th["persona"].best_cost(health) for th in ths.values())
        alt = case["buyer"].get("alternative") or {}
        if alt.get("terms", {}).get("unit_price"):
            best = min(best, total_cost(case, alt["terms"]))
        return best

    def end_open(self, team, reason):
        """End every open session of a team without a deal (agent crashed, or the bank's cap)."""
        with self.lock:
            for s in self._mine(team):
                if s["status"] == "open":
                    self._end(s, reason, None)


def _brief(sig):
    if not sig:
        return None
    keep = ("strongest_ref", "strongest_why", "walk_threat", "deadline", "insult", "condescending", "lowball_tone",
            "appreciation", "payment_promise", "volume_commitment", "asks_breakdown", "react_to", "questions")  # fmt: skip
    out = {k: sig.get(k) for k in keep if sig.get(k)}
    out["claims"] = [{k: c.get(k) for k in ("quote", "ref", "has_figure", "topic", "in_records", "cited")}
                     for c in sig.get("claims") or []]  # fmt: skip
    return out


def serve(sim, host="127.0.0.1", port=8000):
    """Run the API in a background thread; returns the uvicorn server (call .should_exit = True to stop)."""
    import uvicorn

    app = create_app(sim)

    async def more_threads():  # replies that sleep (--reply-delay) must not queue behind anyio's 40 threads
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = 400

    app.router.on_startup.append(more_threads)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError(f"the bank's arena did not start on {host}:{port}")
