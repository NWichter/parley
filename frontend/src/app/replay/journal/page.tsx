import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Kicker, Page, Title } from "@/components/site";
import raw from "./replay.json";

export const metadata: Metadata = {
  title: "Decision journal · Parley",
  description: "Why did the agent write that? One real Parley negotiation, round by round, from its decision journal.",
};

type Terms = Record<string, number | string | null>;
type Ask = { terms: Terms; C: number; value: number };
type Fact = { sentence: string; refs: string[]; verdict: string | null };
type Round = {
  n: number;
  reason: string;
  writer: string;
  c_standing: number;
  analyst?: { tone: string; tactic: string };
  facts: Fact[];
  asks: Ask[];
  model_paragraph: string;
  code_lines: string[];
  reply: { status: string; terms: Terms[]; flags: string[]; drivers_named: string[]; attachments: string[]; text: string; from: string };
};
type Replay = {
  session: string;
  case_id: string;
  label: string;
  agent: string;
  arena_replay: string;
  house_score: number;
  supplier: { name: string };
  buyer: string;
  claim_bank: { kind: string; refs: string[]; sentence: string; strength: number }[];
  opening: { terms: Terms[]; text: string; from: string; c: number; c_alt: number; must_haves?: string };
  rounds: Round[];
  close: { action: { type: string; term_sheet_id: string }; reason: string; c_deal: number };
  result: { score: number; c_open: number; c_deal: number; c_best: number; c_alt: number; terms: Terms };
  arena_rounds: number;
};

const d = raw as unknown as Replay;

const eur = (n: number) => `€${Math.round(n).toLocaleString("en-US")}`;
const price = (n: unknown) => `€${Number(n).toFixed(2)}`;
const ppm = (n: unknown) => `${Number(n).toLocaleString("en-US")} ppm`;

function Tag({ kind }: { kind: "code" | "model" | "arena" }) {
  const cls = {
    code: "bg-foreground text-background",
    model: "bg-primary text-primary-foreground",
    arena: "border border-foreground text-foreground",
  }[kind];
  return <span className={`inline-block px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest ${cls}`}>{kind}</span>;
}

function Label({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">{children}</p>;
}

function Sheet({ terms, c, value, tag }: { terms: Terms; c?: number; value?: number; tag?: string }) {
  return (
    <div className="flex flex-col gap-1 border-2 border-border bg-background p-3 font-mono text-xs">
      {tag && <span className="text-[10px] uppercase tracking-widest text-muted-foreground">{tag}</span>}
      <span className="font-heading text-2xl font-bold leading-none text-signal-ink">{price(terms.unit_price)}</span>
      <span>{ppm(terms.ppm_commitment)}</span>
      <span>chargeback {Number(terms.chargeback_eur_per_complaint ?? 0) ? `€${Number(terms.chargeback_eur_per_complaint)}/complaint` : "none"}</span>
      {c !== undefined && <span className="mt-1 border-t border-dashed border-border pt-1">C for us {eur(c)}</span>}
      {value !== undefined && <span className="text-muted-foreground">worth to them ≈ {price(value)}/unit</span>}
    </div>
  );
}

const verdictStyle: Record<string, string> = {
  verified: "bg-primary text-primary-foreground",
  duplicate: "bg-muted text-foreground",
  contradicted: "bg-foreground text-background",
};

function Verdict({ v }: { v: string | null }) {
  if (!v) return <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">no verdict</span>;
  return <span className={`px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest ${verdictStyle[v] ?? ""}`}>{v === "verified" ? "✓ " : ""}{v}</span>;
}

function Chart() {
  const sup = [d.opening.terms[0].unit_price, ...d.rounds.map((r) => r.reply.terms[0].unit_price)].map(Number);
  const ask = d.rounds.map((r) => Number(r.asks[0].terms.unit_price));
  const deal = Number(d.result.terms.unit_price);
  const W = 440, H = 260, L = 40, R = 14, T = 22, B = 40;
  const n = sup.length;
  const lo = 42.5, hi = 47.5;
  const x = (i: number) => L + (i * (W - L - R)) / n;
  const y = (v: number) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const step = (pts: [number, number][]) =>
    pts.map(([i, v], k) => (k === 0 ? `M${x(i)} ${y(v)}` : `H${x(i)}V${y(v)}`)).join("") + `H${x(n)}`;
  const supPath = step(sup.map((v, i) => [i, v]));
  const askPath = `${step(ask.map((v, i) => [i + 1, v]))}V${y(deal)}`;
  const ticks = [43, 44, 45, 46, 47];
  return (
    <figure className="mx-auto flex w-full max-w-3xl flex-col gap-3 border-2 border-border bg-card p-4 sm:p-5">
      <figcaption className="flex flex-wrap items-center justify-between gap-2">
        <Label>Unit price per round · €</Label>
        <span className="flex flex-wrap gap-4 font-mono text-[10px] uppercase tracking-widest">
          <span className="flex items-center gap-2"><i className="h-0.5 w-5 bg-foreground" />Supplier&apos;s standing offer</span>
          <span className="flex items-center gap-2"><i className="h-0.5 w-5 bg-primary" />Our ask (package 1)</span>
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Supplier price ${sup.map(price).join(", ")}; our ask ${ask.map(price).join(", ")}; deal at ${price(deal)}`}>
        <g className="font-mono" fontSize="14" fill="var(--muted-foreground)">
          {ticks.map((t) => (
            <g key={t}>
              <path d={`M${L} ${y(t)}H${W - R}`} stroke="rgba(22,25,29,0.12)" strokeWidth="1" />
              <text x={L - 8} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          {sup.map((_, i) => (
            <text key={i} x={x(i)} y={H - B + 22} textAnchor={i === 0 ? "start" : "middle"}>{i === 0 ? "open" : `R${i}`}</text>
          ))}
          <text x={x(n)} y={H - B + 22} textAnchor="middle" fill="var(--foreground)" fontWeight="700">deal</text>
        </g>
        <path d={`M${L} ${T - 6}V${H - B}H${W - R}`} fill="none" stroke="var(--foreground)" strokeWidth="2" />
        <path d={supPath} fill="none" stroke="var(--foreground)" strokeWidth="3" />
        <path d={askPath} fill="none" stroke="var(--primary)" strokeWidth="3" strokeDasharray="7 5" />
        {sup.map((v, i) => <rect key={`s${i}`} x={x(i) - 4} y={y(v) - 4} width="8" height="8" fill="var(--foreground)" />)}
        {ask.map((v, i) => <rect key={`a${i}`} x={x(i + 1) - 4} y={y(v) - 4} width="8" height="8" fill="var(--primary)" />)}
        <rect x={x(n) - 8} y={y(deal) - 8} width="16" height="16" fill="var(--primary)" stroke="var(--foreground)" strokeWidth="2" />
        <g className="font-mono" fontSize="13" fontWeight="700">
          <text x={x(0) + 8} y={y(sup[0]) - 8} fill="var(--foreground)">{ppm(d.opening.terms[0].ppm_commitment)}</text>
          <text x={x(1) + 8} y={y(sup[1]) - 8} fill="var(--foreground)">300 ppm</text>
          <text x={x(n) - 12} y={y(deal) - 14} textAnchor="end" fill="var(--signal-ink)">{price(deal)}</text>
        </g>
      </svg>
      <p className="text-xs leading-relaxed text-muted-foreground">
        The supplier opened at {ppm(d.opening.terms[0].ppm_commitment)}; from round 1 every sheet on both sides is at 300 ppm, so the prices compare like for like.
      </p>
    </figure>
  );
}

function Supplier({ text, from, terms, status, c, flags }: { text: string; from: string; terms: Terms[]; status: string; c?: number; flags?: string[] }) {
  return (
    <article className="flex flex-col gap-3 border-2 border-border bg-card p-4 sm:mr-16 sm:p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <Label>Supplier · {from}</Label>
        <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          {status.replace("_", " ")} · condensed
        </span>
      </header>
      <p className="leading-relaxed text-foreground/85">{text}</p>
      <div className="flex flex-wrap items-start gap-3">
        {terms.map((t, i) => (
          <div key={i} className="w-[calc(50%-0.375rem)] sm:w-44">
            <Sheet terms={t} tag={i === 0 ? "standing offer" : "alternative"} c={i === 0 ? c : undefined} />
          </div>
        ))}
        {flags && flags.length > 0 && (
          <div className="flex flex-col gap-1">
            <Label>Read by rules</Label>
            <div className="flex flex-wrap gap-1">
              {flags.map((f) => (
                <span key={f} className="border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase">{f.replace("_", " ")}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function Decision({ r }: { r: Round }) {
  const cs = r.asks.map((a) => a.C);
  const spread = Math.max(...cs) - Math.min(...cs);
  return (
    <article className="flex flex-col gap-5 border-2 border-primary bg-card p-4 shadow-[8px_8px_0_var(--accent)] sm:ml-16 sm:p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <Label>Parley · {d.buyer}</Label>
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-signal-ink">Round {r.n}</span>
      </header>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2"><Tag kind="code" /><Label>Reason in the journal</Label></div>
        <p className="font-heading text-2xl font-bold uppercase leading-tight sm:text-3xl">{r.reason}</p>
        {r.analyst && (
          <p className="font-mono text-xs text-muted-foreground">
            Read of the last reply: tone <b className="text-foreground">{r.analyst.tone}</b>, tactic <b className="text-foreground">{r.analyst.tactic}</b> <Tag kind="model" />
          </p>
        )}
      </div>

      {r.facts.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2"><Tag kind="code" /><Label>Facts cited · verdict by the supplier</Label></div>
          <ul className="flex flex-col gap-2">
            {r.facts.map((f) => (
              <li key={f.sentence} className="flex flex-col gap-1.5 border-l-4 border-foreground bg-background px-3 py-2">
                <span className="text-sm leading-snug">{f.sentence.replace(/\s*\([^)]*:[^)]*\)\.?$/, ".")}</span>
                <span className="flex flex-wrap items-center gap-1.5">
                  {f.refs.map((ref) => (
                    <code key={ref} className="break-all bg-muted px-1.5 py-0.5 font-mono text-[11px]">{ref}</code>
                  ))}
                  <Verdict v={f.verdict} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Tag kind="code" />
          <Label>Packages offered · equal cost for us</Label>
          <span className="font-mono text-[10px] uppercase tracking-widest text-signal-ink">C within {eur(spread)}</span>
        </div>
        <div className={`grid gap-3 ${r.asks.length > 2 ? "grid-cols-2 sm:grid-cols-3" : "grid-cols-2"}`}>
          {r.asks.map((a, i) => (
            <Sheet key={i} terms={a.terms} c={a.C} value={a.value} tag={`package ${i + 1}`} />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2"><Tag kind="model" /><Label>The one paragraph the model wrote</Label></div>
        <p className="border-l-4 border-primary bg-accent/60 px-3 py-2 text-sm italic leading-relaxed">{r.model_paragraph}</p>
        <details className="group">
          <summary className="cursor-pointer font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground hover:text-primary">
            Show the {r.code_lines.length} lines code added
          </summary>
          <ul className="mt-2 flex flex-col gap-1 border-l-4 border-foreground bg-background px-3 py-2 font-mono text-[11px] leading-relaxed">
            {r.code_lines.map((l) => <li key={l} className="break-words">{l}</li>)}
          </ul>
        </details>
      </div>
    </article>
  );
}

export default function ReplayPage() {
  const res = d.result;
  const open = d.opening.terms[0];
  const facts = [
    { label: "Case", value: `${d.case_id.replace("case:", "")} · quality` },
    { label: "Rounds", value: String(d.arena_rounds) },
    { label: "Price", value: `${price(open.unit_price)} → ${price(res.terms.unit_price)}` },
    { label: "Total cost C", value: `${eur(res.c_open)} → ${eur(res.c_deal)}` },
    { label: "Score", value: res.score.toFixed(2) },
    { label: "Tacto house agent", value: d.house_score.toFixed(2) },
  ];
  const used = new Set(d.rounds.flatMap((r) => r.facts.map((f) => f.sentence)));

  return (
    <Page>
      <Title kicker={d.label} title="Why did the agent" accent="write that?">
        One real negotiation, round by round, rebuilt from Parley&apos;s decision journal. {d.supplier.name}, a foundry
        short on cash, supplies bearing brackets to Hollerau Pumpenwerke and opens at {ppm(open.ppm_commitment)}. For
        every e-mail you see the reason code logged, the facts it cited and how the supplier judged them, the packages it
        offered, and the one paragraph a model wrote in this early LLM-mode run. The final build writes every e-mail
        from templates, with no model: on paired games the model moved the score by +0.0001.
      </Title>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {facts.map((f) => (
          <div key={f.label} className="border-2 border-border bg-card p-3">
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{f.label}</p>
            <p className="font-heading text-xl font-bold uppercase leading-tight break-words">{f.value}</p>
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Who wrote what</Kicker>
        <div className="grid gap-3 sm:grid-cols-3">
          <p className="flex flex-col items-start gap-2 border-2 border-border bg-card p-4 text-sm leading-relaxed">
            <Tag kind="code" />
            Every number, data ID, term sheet, cost C, reason and the accept. Deterministic: the same inputs give the same e-mail.
          </p>
          <p className="flex flex-col items-start gap-2 border-2 border-border bg-card p-4 text-sm leading-relaxed">
            <Tag kind="model" />
            One paragraph per e-mail, with no figures, and a tone/tactic tag on each reply. Without a model, templates fill in.
          </p>
          <p className="flex flex-col items-start gap-2 border-2 border-border bg-card p-4 text-sm leading-relaxed">
            <Tag kind="arena" />
            The supplier: its replies, its term sheets and its verdict on each cited fact (verified, duplicate, contradicted).
          </p>
        </div>
      </section>

      <Chart />

      <section className="flex flex-col gap-4">
        <Kicker>What Parley knew before the first e-mail</Kicker>
        <div className="overflow-hidden border-2 border-border bg-card">
          <ul className="divide-y-2 divide-border">
            {d.claim_bank.map((c) => (
              <li key={c.sentence} className="grid gap-1 p-3 sm:grid-cols-[7rem_1fr_6rem] sm:items-baseline sm:gap-4">
                <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">{c.kind.replace("_", " ")} · {c.strength}</span>
                <span className="text-sm leading-snug">{c.sentence}</span>
                <span className={`font-mono text-[10px] font-bold uppercase tracking-widest ${used.has(c.sentence) ? "text-signal-ink" : "text-muted-foreground"}`}>
                  {used.has(c.sentence) ? `cited R${d.rounds.find((r) => r.facts.some((f) => f.sentence === c.sentence))?.n}` : "not cited"}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          The claim bank from the dossier, with code&apos;s strength score. Each fact goes out once; the supplier marks a
          repeated kind of fact as duplicate.
        </p>
      </section>

      <section className="flex flex-col gap-5">
        <Kicker>Round by round</Kicker>
        <Supplier text={d.opening.text} from={d.opening.from.split(",").slice(-1)[0].trim()} terms={d.opening.terms} status="opening offer" c={d.opening.c} />
        {d.opening.must_haves && (
          <p className="font-mono text-xs text-muted-foreground sm:mr-16">
            Code: the opening offer breaks a must-have; the alternative without a deal costs {eur(d.opening.c_alt)}.
          </p>
        )}
        {d.rounds.map((r) => (
          <div key={r.n} className="flex flex-col gap-5">
            <Decision r={r} />
            <Supplier
              text={r.reply.text}
              from={r.reply.from.split(",").slice(-1)[0].trim()}
              terms={r.reply.terms}
              status={r.reply.status}
              c={d.rounds[r.n]?.c_standing ?? d.close.c_deal}
              flags={r.reply.flags}
            />
          </div>
        ))}
        <article className="flex flex-col gap-3 border-2 border-primary bg-foreground p-4 text-background sm:ml-16 sm:p-5">
          <header className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-background/70">Parley · close</span>
            <Tag kind="code" />
          </header>
          <p className="font-heading text-2xl font-bold uppercase leading-tight sm:text-3xl">
            {d.close.action.type} {d.close.action.term_sheet_id}
          </p>
          <p className="font-mono text-xs leading-relaxed text-background/80">{d.close.reason}</p>
          <p className="text-sm leading-relaxed text-background/90">
            Deal at {price(res.terms.unit_price)}, {ppm(res.terms.ppm_commitment)}, {Number(res.terms.volume_per_year).toLocaleString("en-US")} pieces a year
            for {res.terms.contract_months} months. Total cost {eur(res.c_open)} → {eur(res.c_deal)}; the best possible deal was{" "}
            {eur(res.c_best)}, so Parley captured {Math.round(res.score * 100)}% of the possible saving. Tacto&apos;s house agent scored{" "}
            {d.house_score.toFixed(2)} on this case.
          </p>
        </article>
      </section>

      <section className="flex flex-col gap-4">
        <Kicker>Where this comes from</Kicker>
        <div className="grid gap-3 border-2 border-border bg-card p-5 text-sm leading-relaxed sm:grid-cols-2">
          <p>
            <b>The decisions</b> are copied at build time from the journal Parley writes during play (session{" "}
            <span className="font-mono text-xs">{d.session}</span>): reasons, claims, verdicts, the cost C of every sheet
            and the model&apos;s paragraph, unedited. One practice session of {d.agent}, case {d.case_id.replace("case:", "")}, one
            attempt; not the live final.
          </p>
          <p>
            <b>The supplier&apos;s e-mails</b> are shortened to their key sentences (marked condensed); its term sheets are
            exact. Score and cost come from the arena&apos;s evaluation, next to Tacto&apos;s house agent score.
            &quot;Worth to them&quot; is Parley&apos;s own estimate.{" "}
            <a href={d.arena_replay} className="underline decoration-primary decoration-2 underline-offset-4">Arena replay</a>.
          </p>
        </div>
      </section>
    </Page>
  );
}
