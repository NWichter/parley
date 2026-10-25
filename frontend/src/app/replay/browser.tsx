"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Kicker } from "@/components/site";
import { caseKey } from "./cases";
import { BubblePlayer } from "./bubbles";
import { Cast } from "./player";
import type { Collections, Entry, Generation, Replay } from "./types";

const JOURNAL_SESSION = "3c38a30d93a9bc06";
const eur = (n?: number | null) => (n === null || n === undefined ? "–" : `€${Math.round(n).toLocaleString("en-US")}`);
const score = (n?: number | null) => (n === null || n === undefined ? "–" : n.toFixed(2));
const caseShort = (c: string) => c.replace("case:", "");

type Fallback = { entries: Entry[]; replays: Record<string, Replay>; collections: Collections };

type View =
  | { kind: "loading" }
  | { kind: "ready"; data: Replay; source: "api" | "static" }
  | { kind: "none" | "generating" | "failed" | "offline"; error?: string; generation?: Generation };

async function getJson(url: string, init?: RequestInit) {
  const r = await fetch(url, { cache: "no-store", ...init });
  const ct = r.headers.get("content-type") ?? "";
  if (!ct.includes("json")) throw new Error(`${r.status}`);
  const body = await r.json();
  if (r.status >= 500 && r.status !== 503) throw new Error(`${r.status}`);
  return { status: r.status, body };
}

function StatusBadge({ status }: { status?: string }) {
  if (status === "ready")
    return <span className="bg-primary px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest text-primary-foreground">▶ voiced</span>;
  if (status === "generating")
    return <span className="border border-primary px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest text-signal-ink">generating…</span>;
  return <span className="border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">text only</span>;
}

function SessionList({
  entries,
  known,
  live,
  collections,
}: {
  entries: Entry[];
  known: Entry[];
  live: boolean;
  collections: Collections;
}) {
  const tabs = useMemo(
    () => Object.entries(collections).filter(([, c]) => c.sessions.length) as [string, Collections[string]][],
    [collections],
  );
  const [tab, setTab] = useState<string | null>(null);
  const current = tab ?? tabs[0]?.[0] ?? "best";
  const ids = collections[current]?.sessions;
  const runsByCase = new Map(entries.map((e) => [caseKey(e), e.runs]));
  const byId = new Map<string, Entry>(
    [...entries, ...known].map((e): [string, Entry] => [e.session_id, { ...e, runs: e.runs ?? runsByCase.get(caseKey(e)) }]),
  );
  const rows = (ids ? ids.map((id) => byId.get(id)).filter((e): e is Entry => Boolean(e)) : entries).sort((a, b) =>
    `${a.set}|${a.case_id}`.localeCompare(`${b.set}|${b.case_id}`, "en", { numeric: true }),
  );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {tabs.map(([k, c]) => (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              className={`border-2 px-3 py-1.5 text-left font-mono text-xs font-semibold uppercase tracking-widest ${
                k === current ? "border-foreground bg-foreground text-background" : "border-border bg-card hover:border-primary"
              }`}
            >
              {c.label} · {c.sessions.length}
            </button>
          ))}
        </div>
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          {collections[current]?.mean != null ? `mean score ${collections[current].mean?.toFixed(3)} over the ten practice cases · an earlier build (v1.33); final build v1.36: 0.792` : "one replay per case · our best automatic run"}
        </p>
      </div>
      {!live && (
        <p className="border-l-4 border-primary bg-accent/60 px-3 py-2 text-sm">
          The live list is not reachable right now; showing the replays that ship with the site.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {rows.map((e) => {
          const delta = e.score !== null && e.score !== undefined && e.house_score !== null && e.house_score !== undefined ? e.score - e.house_score : null;
          return (
            <li key={e.session_id}>
              <Link
                href={`/replay?id=${e.session_id}`}
                className="grid gap-x-4 gap-y-2 border-2 border-border bg-card p-3 transition-colors hover:border-primary sm:grid-cols-[5.5rem_1fr_auto] sm:items-center sm:p-4"
              >
                <span className="flex items-center gap-3 sm:flex-col sm:items-start sm:gap-1">
                  <span className="font-heading text-2xl font-bold uppercase leading-none">{caseShort(e.case_id)}</span>
                  <StatusBadge status={e.status} />
                </span>
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="text-sm font-semibold leading-snug">{e.title ?? e.case_id}</span>
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {(e.suppliers ?? []).filter(Boolean).join(" · ") || "–"}
                  </span>
                </span>
                <span className="flex flex-wrap items-baseline gap-x-4 gap-y-1 font-mono text-xs sm:justify-end">
                  <span>
                    <span className="text-muted-foreground">score </span>
                    <b className="text-base">{score(e.score)}</b>
                  </span>
                  {delta !== null && (
                    <span className={delta >= 0 ? "text-signal-ink" : "text-muted-foreground"}>
                      {delta >= 0 ? "+" : ""}
                      {delta.toFixed(2)} vs house
                    </span>
                  )}
                  <span className="text-muted-foreground">{e.rounds ?? "–"} rds</span>
                  {e.runs && e.runs > 1 && <span className="text-muted-foreground">best of {e.runs} runs</span>}
                  <span className="text-muted-foreground">{e.deal ? "deal" : (e.end_reason ?? "no deal").replace("_", " ")}</span>
                </span>
              </Link>
            </li>
          );
        })}
        {rows.length === 0 && <li className="border-2 border-dashed border-border p-4 text-sm text-muted-foreground">No replays here yet.</li>}
      </ul>
    </section>
  );
}

function Facts({ d }: { d: Replay }) {
  const r = d.result;
  const facts = [
    { label: "Case", value: `${caseShort(d.case_id)}${d.type ? ` · ${d.type.replace("_", " ")}` : ""}` },
    { label: "Rounds", value: String(d.rounds ?? "–") },
    { label: "Outcome", value: r.deal ? `deal ${r.terms?.unit_price !== undefined ? `€${Number(r.terms?.unit_price).toFixed(2)}` : ""}` : (d.end_reason ?? "no deal").replace("_", " ") },
    { label: "Total cost C", value: r.c_deal ? `${eur(r.c_open)} → ${eur(r.c_deal)}` : eur(r.c_open) },
    { label: "Score", value: score(r.score) },
    { label: "Tacto house agent", value: score(d.house_score) },
  ];
  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {facts.map((f) => (
        <div key={f.label} className="min-w-0 border-2 border-border bg-card p-3">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{f.label}</p>
          <p className="font-heading text-xl font-bold uppercase leading-tight break-words">{f.value}</p>
        </div>
      ))}
    </section>
  );
}

function Detail({ sid, fallback, only, start }: { sid: string; fallback: Fallback; only?: string | null; start?: number | null }) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const entry = fallback.entries.find((e) => e.session_id === sid);

  const load = useCallback(async () => {
    const shipped = async () => {
      const local = fallback.replays[sid];
      if (local) return local;
      if (entry?.status !== "ready" && entry?.status !== "text") return null;
      try {
        const data = (await (await fetch(`/replays/${sid}/replay.json`)).json()) as Replay;
        return { ...data, audio: data.audio ? `/replays/${sid}/audio.mp3` : "" };
      } catch {
        return null;
      }
    };
    let body: { status: string; replay?: Replay; error?: string; generation?: Generation } | null = null;
    try {
      body = (await getJson(`/api/replays/${sid}`)).body;
    } catch {}
    if (body?.status === "ready" && body.replay) {
      setView({ kind: "ready", data: body.replay, source: "api" });
      return;
    }
    if (body?.status !== "generating") {
      const local = await shipped();
      if (local) {
        setView({ kind: "ready", data: local, source: "static" });
        return;
      }
    }
    if (!body) setView({ kind: "offline" });
    else setView({ kind: body.status as "none" | "generating" | "failed", error: body.error, generation: body.generation });
  }, [sid, fallback, entry]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (view.kind !== "generating") return;
    const t = setInterval(() => void load(), 4000);
    return () => clearInterval(t);
  }, [view.kind, load]);

  async function generate() {
    setBusy(true);
    try {
      const { status, body } = await getJson(`/api/replays/${sid}`, { method: "POST" });
      if (status >= 400) setView({ kind: "failed", error: body.detail ?? `error ${status}` });
      else if (body.status === "ready") await load();
      else setView({ kind: "generating" });
    } catch {
      setView({ kind: "offline" });
    } finally {
      setBusy(false);
    }
  }

  const back = (
    <Link href="/replay" className="font-mono text-xs font-semibold uppercase tracking-widest hover:text-primary">
      ← All cases
    </Link>
  );

  if (view.kind !== "ready") {
    const gen = view.kind === "none" || view.kind === "failed" ? view.generation : undefined;
    const canVoice = Boolean(gen?.public && gen.enabled);
    return (
      <section className="flex flex-col gap-5">
        {back}
        <h1 className="font-heading text-4xl font-bold uppercase leading-none break-words sm:text-6xl">
          Session <span className="text-primary">{sid}</span>
        </h1>
        <div className="flex flex-col gap-3 border-2 border-border bg-card p-5">
          {view.kind === "loading" && <p className="font-mono text-sm">Loading…</p>}
          {view.kind === "offline" && <p>The replay service is not reachable right now. Voiced replays that ship with the site still play.</p>}
          {view.kind === "generating" && (
            <p className="flex items-center gap-3">
              <span className="h-3 w-3 animate-pulse bg-primary" />
              Fetching the arena replay, condensing the e-mails and voicing them. This takes about a minute.
            </p>
          )}
          {(view.kind === "none" || view.kind === "failed") && (
            <>
              {view.kind === "failed" && <p className="font-mono text-sm text-signal-ink">{view.error}</p>}
              <p>
                This negotiation has not been voiced yet.
                {canVoice
                  ? " One click fetches the finished replay from the arena and voices every e-mail, one voice per party; the result is cached for everyone."
                  : " We voice the best run per case ourselves; check back soon."}
              </p>
              {canVoice && (
                <div className="flex flex-wrap items-center gap-4">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void generate()}
                    className="border-2 border-foreground bg-primary px-4 py-2 font-mono text-xs font-bold uppercase tracking-widest text-primary-foreground disabled:opacity-50"
                  >
                    {view.kind === "failed" ? "Try again" : "Voice this replay"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    );
  }

  const d = view.data;
  const suppliers = Object.values(d.speakers).filter((s) => s.kind === "supplier");
  return (
    <>
      <section className="flex flex-col gap-5">
        {back}
        <Kicker>
          {d.set} session · {d.agent ?? "Parley"}
        </Kicker>
        <h1 className="font-heading text-4xl font-bold uppercase leading-[0.95] break-words sm:text-6xl">
          {d.title ?? caseShort(d.case_id)}
        </h1>
        <p className="max-w-3xl text-lg leading-relaxed text-foreground/80">
          Hollerau Pumpenwerke against {suppliers.map((s) => s.company).join(", ").replace(/\.$/, "")}. Every e-mail of the arena replay
          {d.audio ? ", voiced," : ""} with greetings and boilerplate left out{d.audio ? ", one voice per party" : ""}; the term sheets and Parley&apos;s logged reason for each move
          sit next to the words.
        </p>
      </section>
      <Facts d={d} />
      <Cast data={d} />
      <BubblePlayer data={d} only={only} start={start} />
      <section className="flex flex-col gap-4">
        <Kicker>How this was made</Kicker>
        <div className="grid gap-3 border-2 border-border bg-card p-5 text-sm leading-relaxed sm:grid-cols-2">
          <p>
            <b>The words and numbers are the arena&apos;s.</b> Each e-mail is trimmed by fixed rules: greetings, signatures
            and boilerplate go, everything else stays, word for word. Term sheets are the binding
            ones from the replay; cost and score come from the arena&apos;s evaluation, next to Tacto&apos;s house agent score.
            Reasons are copied from Parley&apos;s decision journal where we have it.
          </p>
          {d.audio ? (
          <p>
              <b>The voices are a re-enactment.</b> ElevenLabs text-to-dialogue ({d.model_id ?? "eleven_v3"}
              {d.characters ? `, ${d.characters.toLocaleString("en-US")} characters` : ""}). Each party has its own voice,
              with no accent and nothing taken from country or company size; a contact is named only where the
              arena&apos;s own mails sign with a name. The per-turn timestamps
              drive the transcript.{" "}
              {d.replay_url && (
                <a href={d.replay_url} className="underline decoration-primary decoration-2 underline-offset-4">
                  Original arena replay
                </a>
              )}
              {sid === JOURNAL_SESSION && (
                <>
                  {" · "}
                  <Link href="/replay/journal" className="underline decoration-primary decoration-2 underline-offset-4">
                    Full decision journal
                  </Link>
                </>
              )}
            </p>
          ) : (
            <p>
              <b>Voices come later.</b> This replay is text only for now; the voiced version (ElevenLabs, one voice per party)
              is added after the final.{" "}
              {d.replay_url && (
                <a href={d.replay_url} className="underline decoration-primary decoration-2 underline-offset-4">
                  Original arena replay
                </a>
              )}
            </p>
          )}
        </div>
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          {d.audio ? `Voices: ElevenLabs${view.source === "static" ? " · served from the site" : ""}` : "Text replay · served from the site"}
        </p>
      </section>
    </>
  );
}

export function ReplayBrowser({ fallback }: { fallback: Fallback }) {
  const params = useSearchParams();
  const router = useRouter();
  const sid = params.get("id");
  const [entries, setEntries] = useState<Entry[]>(fallback.entries);
  const [live, setLive] = useState<boolean | null>(null);

  useEffect(() => {
    if (sid) return;
    let gone = false;
    getJson("/api/replays")
      .then(({ body }) => {
        if (gone) return;
        const rows = (body.sessions as Entry[]) ?? [];
        if (!rows.length) throw new Error("empty");
        const known = new Set(rows.map(caseKey));
        setEntries([...rows, ...fallback.entries.filter((e) => !known.has(caseKey(e)))]);
        setLive(true);
      })
      .catch(() => {
        if (gone) return;
        setEntries(fallback.entries);
        setLive(false);
      });
    return () => {
      gone = true;
    };
  }, [sid, fallback]);

  useEffect(() => {
    if (sid && !/^[0-9a-f]{16}$/.test(sid)) router.replace("/replay");
  }, [sid, router]);

  const only = params.get("thread");
  const start = params.get("step");
  if (sid) return <Detail key={sid} sid={sid} fallback={fallback} only={only} start={start ? Number(start) : null} />;
  return (
    <>
      <section className="flex flex-col gap-5">
        <Kicker>Replays · best run per case</Kicker>
        <h1 className="font-heading text-5xl font-bold uppercase leading-[0.92] break-words sm:text-7xl">
          Watch our
          <br />
          <span className="text-primary">negotiations.</span>
        </h1>
        <div className="max-w-3xl text-lg leading-relaxed text-foreground/80">
          Parley&apos;s best automatic run on each Tacto arena case, with its score and the house agent&apos;s score on the same
          case. Open one and press play: every e-mail becomes a speech bubble, with the binding term sheets and Parley&apos;s
          reason for each move. A second tab shows one complete round: a single version on all ten practice cases.
        </div>
      </section>
      {live === null && <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Loading the cases from the arena…</p>}
      <SessionList entries={entries} known={fallback.entries} live={live !== false} collections={fallback.collections} />
    </>
  );
}
