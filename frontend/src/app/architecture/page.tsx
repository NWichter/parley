import type { Metadata } from "next";
import { Kicker, Page, Title } from "@/components/site";

export const metadata: Metadata = { title: "Architecture · Parley" };

const flow: { name: string; file: string; text: string }[] = [
  {
    name: "Data",
    file: "parley data",
    text: "Tacto's data package and the Sunday release, checksummed, plus the case file: need, goal, must-haves, alternative.",
  },
  {
    name: "Dossier + profiles",
    file: "dossier.py",
    text: "Every fact Parley may cite, with the data ID the arena checks, and a supplier profile prepared offline with its evidence quotes.",
  },
  {
    name: "Valuation",
    file: "valuation.py",
    text: "Total cost C over the contract for every standing and possible sheet, per lever, including the supplier-health charge, the index clause and a cost of money per supplier.",
  },
  {
    name: "Policy + guards",
    file: "policy.py · rules.py",
    text: "Anchor, concession steps, packages that cost us the same, one new fact per round, accept or walk. Guards G1–G9 fence every move.",
  },
  {
    name: "Mail templates",
    file: "writer.py",
    text: "Templates write the words; code fills in every figure, data ID and sheet.",
  },
  {
    name: "Arena client",
    file: "runner.py",
    text: "Brings the quotes and every reply. Sends with a move ID that wait up to 180 s, 409 pickup, 429 backoff, one worker per supplier thread, a journal entry per decision.",
  },
];

const modules: { n: string; name: string; tag?: string; text: string }[] = [
  {
    n: "01",
    name: "Dossier",
    text: "Built per supplier before the first e-mail, from the data package (orders, invoices, scorecards, contracts, letters, call notes, news, market indices) and the Sunday release. Every fact carries its data ID, checked against facts.json before it goes into a mail. Release items are read the same way: inputs “bought forward” count as hedged, a lost customer in the news as free capacity. If the dossier cannot be built, it is retried once and the case plays on a plain profile (G9).",
  },
  {
    n: "02",
    name: "Supplier profiles",
    tag: "verified offline data",
    text: "Prepared once, offline, from Tacto's data package only. The profiles give patience, levers and the cash and hedge signals the policy uses; a cash or hedge flag is kept only if its evidence quote is found in the data. 17 profile fields per detailed supplier (approval threshold, levers, capacity, price freeze, cash need, hedged inputs, who signs) carry a source ID and a verbatim quote: no quote, no value. A second model family cross-checked all 46 suppliers, a third 10. Most field-based switches are off in the final because they did not pass our gate.",
  },
  {
    n: "03",
    name: "Valuation",
    text: "The arena's published cost formula, with our estimates for what it does not show: price × volume, payment interest, freight by Incoterm, late lead time, expected defects minus chargebacks, tooling, uncovered need and fallback risk, index clause path and risk, and on relationship goals the supplier-health charge (supplier full cost estimated as should-cost / 1.02; it matched the arena within 1 % on our own sessions). It also prices what a package is worth to the supplier, with a cost of money from documented evidence and the payment terms it picks; stock phrases do not count.",
  },
  {
    n: "04",
    name: "Policy",
    text: "Opening anchor (deeper for leverage and RFQs, never below the estimated floor; on relationship goals a further 3 % of the opening price lower). Steps react to the supplier: less after a big step, almost nothing after an announced internal step, more when it holds. Sheets in one mail cost us the same and differ in one lever (three in round 1, two later, one when closing), so the supplier's choice shows what it values. Two facts in round 1, then one new fact per round. The close takes the cheapest standing offer that keeps every must-have and beats the alternative, or walks.",
  },
  {
    n: "05",
    name: "Reader + writer",
    text: "Rules read each reply: status, claim verdicts, signal sentences. Templates pick an opening for the situation (price letter, renewal, RFQ, capacity, quality), never repeat within a thread and name the concern the supplier raised. An optional model layer exists (--mode hybrid reads, --mode full also writes one paragraph without digits). The final runs --no-llm: on 90 paired games the layer moved the score by +0.0001, while a decision took 7.4 s instead of 0.2 s (p95).",
  },
  {
    n: "06",
    name: "Runner + supervisor",
    text: "Plays every case of a wave in parallel, one worker per supplier thread, and closes about 90 s before the 8-minute cap. Restarted after a crash, it picks up its open sessions through the 409. The supervisor restarts it after 3 s and, after three exits in 120 s, switches to the next build of the fallback chain.",
  },
];

const guards: [string, string][] = [
  ["G1", "No counter-offer after a final offer: mirror the supplier's own cheaper alternative or end the thread; the close accepts or walks."],
  ["G2", "RFQ: a clearly cheapest bid within 3 % of our ask, or an agreeing leader, closes the case at once."],
  ["G3", "Acknowledge only a concern the supplier raised (cash, margin)."],
  ["G4", "No index clause as a lever toward a hedged supplier, unless it asked for one."],
  ["G5", "Where the arena charges supplier health, packages start from the estimated health floor."],
  ["G6", "In a thread, a raised cost of money needs a real cash statement or a chosen short-payment sheet."],
  ["G7", "Retries reuse the move ID; a timeout or 409 re-reads the arena before deciding again."],
  ["G8", "The first ask never goes deeper than the depth we tested."],
  ["G9", "Dossier before the first move; on failure, retry once, then a plain profile."],
];

const params: { name: string; value: string; does: string; tested: string }[] = [
  { name: "First-ask depth", value: "6–16 % per goal, RFQ +8, max 22 % (RFQ 26 %)", does: "Where round 1 opens below the supplier's quote.", tested: "Deeper anchors across the board lost on practice cases; only the relationship rule passed." },
  { name: "Relationship depth rule", value: "+3 points", does: "Stabilise and secure-supply goals ask deeper; suppliers often agreed to the old first ask at once.", tested: "Paired bank runs; off in the safe fallback build." },
  { name: "Should-cost clamp", value: "0.94", does: "Never ask below 94 % of our estimate of the supplier's cost.", tested: "Swept 0.92–0.98: 0.98 cost −0.016, 0.92 gained +0.004; kept." },
  { name: "Reciprocity", value: "0.6", does: "Our step = 0.6 × the supplier's step, as a share of the gap.", tested: "Swept 0.4–1.0: −0.004 … +0.007, never +0.01; kept." },
  { name: "Step limits", value: "5–25 % of the gap", does: "15 % after the supplier closed over 40 % of the gap; at least 15 % toward a supplier that keeps moving.", tested: "25 % cap swept: flat (±0.002); mover floor +0.003 in two paired runs." },
  { name: "Price-increase step", value: "≥ 20 % of the gap", does: "Keeps price-increase cases from stalling once the supplier moves.", tested: "Gated with the price-increase rules on bank and sandbox." },
  { name: "Facts in round 1", value: "2, then 1 per round", does: "How much evidence the first mail carries.", tested: "Swept 1–3: 2 is best (1: −0.005, 3: −0.003)." },
  { name: "Cost of money", value: "6.5 / 9.5 / 14 %, in thread 10 %", does: "How much a supplier values faster payment: corporate, family-owned, documented cash need; 10 % after a real cash statement or a chosen short-payment sheet.", tested: "Swept 0.05–0.25; 0.10 adopted (bank +0.006, practice P04 +0.058)." },
  { name: "RFQ take", value: "within 3 %, lead > 5 %", does: "A clearly cheapest bid near our ask is taken and the case closes; bidders over 8 % behind are parked.", tested: "Bank +0.0006 / +0.0037, no new zero; practice P05 0.659 → 0.865." },
  { name: "Round-1 rival claim", value: "on", does: "The cheapest rival price goes to every dearer bidder from round 1.", tested: "Alone it lost on practice (a bidder walked); with the take rule it passed." },
  { name: "Index clause", value: "path capped at ±8 %", does: "The clause is priced in C as the arena prices it. A 1.2 × margin rule and a wider deadband as a lever exist behind a switch, off.", tested: "The switch gained +0.001 on the bank but lost on hold-out practice cases." },
  { name: "Send timeout", value: "180 s", does: "How long a send waits for the supplier's reply, per attempt.", tested: "Live replies p90 61–74 s, max ≈ 100 s; 60 s made the client retry moves still being answered." },
  { name: "Close margin", value: "90 s", does: "The runner starts closing this long before the wave cap.", tested: "Fault rehearsal and a five-wave load test: no case open at the cap." },
];

const chain: [string, string][] = [
  ["rc", "release candidate"],
  ["safe", "same checkout, recent switches reset"],
  ["F1R", "older tagged build"],
  ["F1", "oldest tagged build"],
];

const api: [string, string][] = [
  ["GET /v1/cases?set=…", "case files: need, goal, rates, must-haves, alternative, suppliers"],
  ["POST /v1/sessions", "open a negotiation; round 0 brings every supplier's quote e-mail"],
  ["GET …/attachments/{id}", "quote PDFs and documents, parsed for standard terms"],
  ["POST …/threads/{supplier}/messages", "our e-mail: text, up to three term sheets, data IDs; or accept / walk"],
  ["GET …/threads/{supplier}", "re-sync a thread after a timeout or a 409"],
  ["GET /v1/sessions/{sid}", "result, trace and replay link after the end"],
];

const tests: [string, string][] = [
  ["Persona bank", "9 modelled supplier behaviours × 20 generated held-out cases, scored with the supplier-health charge. A change goes in only if the bank is not lower, no persona drops by more than 0.02, no new zeros, sandbox not lower."],
  ["LLM-voiced suppliers", "The same bank with Claude, Gemini, GPT and DeepSeek models writing the supplier side: ≈ 0.69–0.71 whichever family plays."],
  ["Strength guards", "Our best own practice negotiations must stay reachable: each buyer turn is decided again with the build under test; a worse sheet blocks the merge."],
  ["Sandbox + practice", "The arena's free cases three times per variant; the ten hosted practice cases once per release candidate."],
  ["Mail lint", "Every buyer mail of a bank run: banned phrases, repeated openings, missing contact name, statements the supplier's mail does not back."],
  ["Overfitting guard", "A test fails if the agent's code names a case, supplier ID or supplier name; three practice cases are hold-outs."],
];

const ops: [string, string][] = [
  ["Pre-flight", "Keys, kit, install, tests, arena key, clock, disk, power and network as PASS / WARN / FAIL before anything plays."],
  ["Fault rehearsal", "The final simulated with the real supervisor: runner killed mid-wave, crash loop, 60–120 s supplier delays, 429 bursts, missing release. Pickup 1.7–5.0 s after a kill."],
  ["Export", "The public repo is an allowlisted copy of one commit, scanned for secrets and private paths."],
];

function Arrow({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 pl-[18px]" aria-hidden="true">
      <span className="flex flex-col items-center">
        <span className="h-4 w-0.5 bg-primary" />
        <span className="-mt-1 font-mono text-sm leading-none text-primary">▼</span>
      </span>
      {label && <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">{label}</span>}
    </div>
  );
}

function Cards({ items }: { items: [string, string][] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="border-2 border-border bg-card p-4">
          <p className="font-mono text-xs font-bold uppercase tracking-widest text-signal-ink">{k}</p>
          <p className="mt-1 text-sm leading-relaxed text-foreground/80">{v}</p>
        </div>
      ))}
    </div>
  );
}


function withFlags(text: string) {
  return text.split(/(--[a-z][a-z-]*)/).map((part, i) =>
    i % 2 ? (
      <code key={i} className="font-mono text-[0.9em] [font-variant-ligatures:none]">
        {part}
      </code>
    ) : (
      part
    ),
  );
}

export default function ArchitecturePage() {
  return (
    <Page>
      <Title kicker="Submission · architecture" title="Money, time, protocol:" accent="code.">
        Deterministic code owns every number, every deadline and every API call; templates write the
        e-mails. The Live Final build (v1.38) runs without a language model, because on paired games the
        model layer did not move the score.
      </Title>

      <section className="flex flex-col gap-4">
        <Kicker>Data flow per case</Kicker>
        <ol className="flex flex-col" aria-label="Data flow from the data package to the arena">
          {flow.map((s, i) => (
            <li key={s.name} className="flex flex-col">
              {i > 0 && <Arrow />}
              <div className="grid gap-1 border-2 border-border bg-card p-4 sm:grid-cols-[220px_1fr] sm:gap-4">
                <div className="flex min-w-0 flex-col gap-1">
                  <span className="font-heading text-xl font-bold uppercase leading-none">{s.name}</span>
                  <span className="break-words font-mono text-[11px] text-muted-foreground">{s.file}</span>
                </div>
                <p className="text-sm leading-relaxed text-foreground/80">{s.text}</p>
              </div>
            </li>
          ))}
          <li className="flex flex-col">
            <Arrow label="supplier reply" />
            <div className="border-2 border-dashed border-primary bg-card p-4 text-sm leading-relaxed">
              <span className="font-heading text-xl font-bold uppercase">Reader</span>{" "}
              <span className="font-mono text-xs font-bold uppercase tracking-widest text-signal-ink">↺ loop</span>
              <span className="mt-1 block">
                Reply → reader → valuation → policy, every round, until Parley accepts or walks (sent through the
                arena client). Outside the loop: the profiles were prepared offline, the mail lint runs in our
                tests, and each decision&apos;s reason in the journal feeds the replay.
              </span>
            </div>
          </li>
        </ol>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Modules</Kicker>
        <ol className="grid gap-4">
          {modules.map((m) => (
            <li key={m.n} className="grid gap-3 border-2 border-border bg-card p-5 sm:grid-cols-[180px_1fr]">
              <div className="flex flex-col gap-2">
                <span className="font-mono text-sm font-bold text-signal-ink">{m.n}</span>
                <span className="font-heading text-2xl font-bold uppercase leading-none">{m.name}</span>
                <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">{m.tag ?? "deterministic code"}</span>
              </div>
              <p className="leading-relaxed text-foreground/80">{withFlags(m.text)}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Guards G1–G9, pinned by tests</Kicker>
        <ul className="grid gap-2 sm:grid-cols-3">
          {guards.map(([k, v]) => (
            <li key={k} className="flex gap-3 border-2 border-border bg-card p-3 text-sm leading-relaxed">
              <span className="font-mono font-bold text-signal-ink">{k}</span>
              <span className="min-w-0 text-foreground/80">{v}</span>
            </li>
          ))}
        </ul>
        <p className="text-sm leading-relaxed text-foreground/80">
          Acceptance checks on top: an offer is taken only if it keeps every must-have and costs less than the
          opening and the alternative; an unreadable quote PDF cannot satisfy an open must-have; and a deal
          with a low estimated score is not closed while moves, a fresh fact or an unused lever are left.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>RFQs and time</Kicker>
        <Cards
          items={[
            ["RFQ", "Each bidder in its own thread. First moves are decided in a fixed supplier order before any is sent. From round 1 the cheapest rival price goes to every dearer bidder as a checked claim; bidders more than 8 % behind are parked; a clear best bid within 3 % of our ask is taken, and the case closes at once."],
            ["Time", "60 s per message, 8-minute waves. The runner begins closing about 90 s before the cap, on the best standing offer. Pacing counts from measured round trips how many rounds still fit; only when time binds does the policy take bigger steps, and a mail whose reply would land after the close is not sent."],
          ]}
        />
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Parameters of the final build</Kicker>
        <p className="text-sm leading-relaxed text-foreground/80">
          Set by hand, then tested on our own persona bank (paired, same cases and seeds) and, where noted, on
          practice cases. For the swept ones, no other value met our rule for a change (+0.01 in two runs, no
          persona down by more than 0.02, no new zero), so they stayed.
        </p>
        <ul className="grid gap-3 sm:grid-cols-2">
          {params.map((p) => (
            <li key={p.name} className="flex flex-col gap-2 border-2 border-border bg-card p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="font-heading text-lg font-bold uppercase leading-tight">{p.name}</span>
                <span className="font-mono text-sm font-bold text-signal-ink">{p.value}</span>
              </div>
              <p className="text-sm leading-relaxed text-foreground/80">{p.does}</p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                <span className="font-mono font-bold uppercase tracking-widest">Tested · </span>
                {p.tested}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Fallback chain</Kicker>
        <ol className="flex flex-wrap items-center gap-2" aria-label="Fallback chain of the supervisor">
          {chain.map(([k, v], i) => (
            <li key={k} className="flex items-center gap-2">
              {i > 0 && <span className="font-mono text-primary" aria-hidden="true">→</span>}
              <span className="border-2 border-border bg-card px-3 py-2">
                <span className="font-mono text-sm font-bold">{k}</span>
                <span className="ml-2 text-xs text-muted-foreground">{v}</span>
              </span>
            </li>
          ))}
        </ol>
        <p className="text-sm leading-relaxed text-foreground/80">
          Three exits within 120 s switch to the next build. Each build runs from its own clean checkout with a
          locked environment.
        </p>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Integration: Tacto Negotiation Arena API</Kicker>
        <dl className="grid border-2 border-border bg-card">
          {api.map(([k, v]) => (
            <div key={k} className="grid gap-1 border-b border-border/30 p-3 last:border-0 sm:grid-cols-[280px_1fr] sm:gap-4">
              <dt className="break-all font-mono text-sm font-semibold">{k}</dt>
              <dd className="text-sm text-foreground/80">{v}</dd>
            </div>
          ))}
        </dl>
        <Cards
          items={[
            ["Sends", "Every POST carries an X-Move-Id; HTTP retries of one send reuse it, so the arena sees one move. A send waits up to 180 s per attempt, bounded by the cap."],
            ["409 / timeout", "After a timeout re-read the thread, after a 409 the session and the thread, then decide again from what the arena has. This is also how a restarted runner picks up."],
            ["429 / 5xx", "A short 429 is waited out (Retry-After) and retried; the close is retried with backoff until shortly before the cap."],
            ["Live Final", "No human in the loop; the agent never reads the event feed, screen, leaderboard or results."],
          ]}
        />
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>How we test (our own tests)</Kicker>
        <Cards items={tests} />
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Operations</Kicker>
        <Cards items={ops} />
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Stack</Kicker>
        <Cards
          items={[
            ["Agent", "Python 3.12+, httpx, the arena's published cost function (vendored from the MIT starter kit)."],
            ["Models", "Not in the final's loop. Offline only: supplier profiles and profile fields, cross-checked by other model families."],
            ["Web", "Next.js 16 + FastAPI behind one domain: this site, the deck and the replay viewer."],
            ["Voices", "ElevenLabs text-to-dialogue for the replays; portraits are SVG drawn from data, no image model."],
          ]}
        />
      </section>
    </Page>
  );
}
