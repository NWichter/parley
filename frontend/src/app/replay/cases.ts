import type { Entry } from "./types";

export const caseKey = (e: Entry) => `${e.set}|${e.case_id}`;

export function bestPerCase(entries: Entry[]): Entry[] {
  const best = new Map<string, Entry>();
  for (const e of entries) {
    const cur = best.get(caseKey(e));
    if (!cur || (e.score ?? -1) > (cur.score ?? -1)) best.set(caseKey(e), e);
  }
  return [...best.values()];
}
