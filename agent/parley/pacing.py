"""Time-aware pacing: how many more rounds a thread can still play before the wave's cap, from the
round trips we have measured (per thread, per case and across all cases of this process)."""

import math
import os
import threading
import time

PRIOR_RTT_S, PRIOR_P90_S = 45.0, 75.0
EWMA_ALPHA = 0.35
P90_RATIO = float(os.environ.get("PARLEY_PACE_P90_RATIO") or 1.5)
OWN_MIN, KEEP = 2, 30


def ewma(xs, alpha=EWMA_ALPHA):
    m = xs[0]
    for x in xs[1:]:
        m = alpha * x + (1 - alpha) * m
    return m


def q90(xs):
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(0.9 * len(s)) - 1)]


class Pool:
    def __init__(self):
        self.lock = threading.Lock()
        self.xs = []

    def add(self, x):
        with self.lock:
            self.xs = (self.xs + [x])[-KEEP:]

    def recent(self):
        with self.lock:
            return list(self.xs)


SHARED = Pool()


class Pace:
    def __init__(self, shared=SHARED, clock=None):
        self.shared, self.clock = shared, clock or (lambda: time.time())
        self.own: dict[str, list] = {}
        self.case = Pool()  # every round of this case; the shared pool also holds the other cases'

    def add(self, sid, seconds):
        self.own.setdefault(sid, []).append(seconds)
        self.case.add(seconds)
        if self.shared is not None:
            self.shared.add(seconds)

    def estimate(self, sid):
        """(expected, p90) seconds for one round with this supplier."""
        own = self.own.get(sid, [])[-KEEP:]
        base = own if len(own) >= OWN_MIN else (self.shared or self.case).recent()
        if not base:
            return PRIOR_RTT_S, PRIOR_P90_S
        e = ewma(own) if len(own) >= OWN_MIN else sum(base) / len(base)
        p = q90(base) if len(base) >= 8 else max(max(base), P90_RATIO * e)
        return e, max(p, e)

    def rounds(self, sid, close_at):
        """Rounds we can still start whose reply lands before `close_at`: the next one must fit its
        p90, every later one its expected time. None without a cap."""
        if not close_at:
            return None
        left = close_at - self.clock()
        e, p = self.estimate(sid)
        if left < p:
            return 0
        return 1 + int((left - p) // max(e, 1.0))
