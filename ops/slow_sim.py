"""The kit's arena-sim with slow suppliers: every reply waits a lognormal delay around the median
SIM_REPLY_DELAY seconds (0.3x..3x, sigma SIM_REPLY_SIGMA), as the arena's LLM suppliers do. The wait
does not count against the buyer's 60 seconds and does not block other calls; the same case, thread and
round always wait the same time. Run it from the
starter kit's project: `uv run python ops/slow_sim.py serve --final-rehearsal ...`."""

import math
import os
import random
import time
import zlib

from arena_kit import sim

DELAY = float(os.environ.get("SIM_REPLY_DELAY") or 0)
SIGMA = float(os.environ.get("SIM_REPLY_SIGMA") or 0.45)
_answer = sim.Sim._answer


def slow_answer(self, s, th, msg, sheets):
    if DELAY > 0:
        th["last_at"] = self.clock() + 1e6
        rng = random.Random(zlib.crc32(f"{s['case']['case_id']}|{th['key']}|{th['round']}".encode()))
        wait = min(3 * DELAY, max(0.3 * DELAY, DELAY * math.exp(SIGMA * rng.gauss(0, 1))))
        self.lock.release()
        try:
            time.sleep(wait)
        finally:
            self.lock.acquire()
        th["last_at"] = self.clock()
    return _answer(self, s, th, msg, sheets)


sim.Sim._answer = slow_answer

if __name__ == "__main__":
    print(f"arena-sim: suppliers reply after a median {DELAY:.0f} s (sigma {SIGMA})", flush=True)
    sim.main()
