"""The language models behind Parley, per role: Claude through the Anthropic SDK, or any OpenRouter
model as `openrouter:<model>`. Hard timeouts and token, cost and latency accounting per case. Every
caller has a deterministic fallback, so a slow or failing model never costs a move."""

import json
import logging
import os
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import httpx

log = logging.getLogger("parley")

PRICES = {  # USD per million tokens (input, output)
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ROLES = {
    "plan": ("PLAN_MODEL", "claude-opus-5-5"),
    "writer": ("WRITE_MODEL", "claude-sonnet-5-5"),
    "analyst": ("ANALYST_MODEL", "claude-haiku-5-5"),
}


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 2)


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls, self.fails = [], []

    def add(self, model, role, tin, tout, seconds, tag=None, usd=None):
        with self.lock:
            self.calls.append(
                {"model": model, "role": role, "in": tin, "out": tout, "s": round(seconds, 2), "tag": tag, "usd": usd}
            )

    def fail(self, role, tag=None, why=""):
        with self.lock:
            self.fails.append({"role": role, "tag": tag, "why": why})

    @staticmethod
    def cost_usd(calls):
        total = 0.0
        for c in calls:
            if c.get("usd") is not None:
                total += c["usd"]
                continue
            pin, pout = PRICES.get(c["model"], (4.0, 20.0))
            total += c["in"] / 1e6 * pin + c["out"] / 1e6 * pout
        return round(total, 4)

    def summary(self, tag=None):
        with self.lock:
            calls = [c for c in self.calls if tag is None or c.get("tag") == tag]
            fails = [f for f in self.fails if tag is None or f.get("tag") == tag]
        secs = [c["s"] for c in calls]
        by_role = {}
        for c in calls:
            r = by_role.setdefault(c["role"], {"model": c["model"], "calls": 0, "s": []})
            r["calls"] += 1
            r["s"].append(c["s"])
        return {
            "calls": len(calls),
            "failures": len(fails),
            "timeouts": sum(1 for f in fails if f["why"] == "timeout"),
            "input_tokens": sum(c["in"] for c in calls),
            "output_tokens": sum(c["out"] for c in calls),
            "usd": self.cost_usd(calls),
            "latency_p50": pct(secs, 0.5),
            "latency_p95": pct(secs, 0.95),
            "roles": {
                k: {
                    "model": v["model"],
                    "calls": v["calls"],
                    "p50": pct(v["s"], 0.5),
                    "p95": pct(v["s"], 0.95),
                    "mean": round(statistics.mean(v["s"]), 2),
                }
                for k, v in by_role.items()
            },
        }


class LLM:
    def __init__(self, plan_model=None, write_model=None, analyst_model=None):
        given = {"plan": plan_model, "writer": write_model, "analyst": analyst_model}
        self.models = {role: given[role] or os.environ.get(env, default) for role, (env, default) in ROLES.items()}
        self.anthropic = None
        if any(not m.startswith("openrouter:") for m in self.models.values()):
            import anthropic

            self.anthropic = anthropic.Anthropic(max_retries=0, timeout=40.0)
        self.http = httpx.Client(timeout=60.0)
        self.usage = Usage()
        self.pool = ThreadPoolExecutor(max_workers=16)

    def _claude(self, model, system, user, max_tokens, effort):
        r = self.anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
        )
        if r.stop_reason == "refusal":
            raise RuntimeError("refusal")
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens, None

    def _openrouter(self, model, system, user, max_tokens, effort):
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "usage": {"include": True},
            "reasoning": {"effort": effort},
        }
        r = self.http.post(
            OPENROUTER_URL, json=body, headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"}
        )
        r.raise_for_status()
        data = r.json()
        u = data.get("usage") or {}
        text = data["choices"][0]["message"].get("content") or ""
        return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost")

    def _call(self, model, role, system, user, max_tokens, effort, tag=None):
        t0 = time.monotonic()
        if model.startswith("openrouter:"):
            out = self._openrouter(model.split(":", 1)[1], system, user, max_tokens, effort)
        else:
            out = self._claude(model, system, user, max_tokens, effort)
        text, tin, tout, usd = out
        self.usage.add(model, role, tin, tout, time.monotonic() - t0, tag, usd)
        return text

    def text(self, system, user, role="writer", timeout=15.0, max_tokens=1200, effort="low", tag=None):
        model = self.models[role]
        fut = self.pool.submit(self._call, model, role, system, user, max_tokens, effort, tag)
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            log.warning("%s model timed out after %.0fs", role, timeout)
            self.usage.fail(role, tag, "timeout")
        except Exception as e:  # noqa: BLE001 - any model failure falls back to code
            log.warning("%s model failed: %s", role, e)
            self.usage.fail(role, tag, type(e).__name__)
        return None

    def json(self, system, user, **kw):
        out = self.text(system, user, **kw)
        if not out:
            return None
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


class Tagged:
    """One case's view of the shared model client: every call is booked to that case."""

    def __init__(self, llm, tag):
        self.llm, self.tag = llm, tag
        self.models, self.usage = llm.models, llm.usage

    def text(self, *a, **k):
        return self.llm.text(*a, tag=self.tag, **k)

    def json(self, *a, **k):
        return self.llm.json(*a, tag=self.tag, **k)

    def summary(self):
        return self.usage.summary(self.tag)


def from_env(enabled=True):
    if not enabled:
        return None
    models = [os.environ.get(env, default) for env, default in ROLES.values()]
    needs_claude = any(not m.startswith("openrouter:") for m in models)
    if needs_claude and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        return LLM()
    except Exception as e:  # noqa: BLE001
        log.warning("no LLM: %s", e)
        return None
