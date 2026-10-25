"use client";

import { useEffect, useRef, useState } from "react";
import { lookFor, Mouth, NEUTRAL_LOOK, PARLEY_LOOK, PersonaAvatar, roleOf, type PersonaLook } from "@/components/persona/avatar";
import { fmtTerm } from "@/app/replay/player";
import type { Speaker, Turn } from "@/app/replay/types";

export type StageTurn = Turn & { index: number };

const FEMALE = /^(anna|anne|jana|claudia|maria|marie|eva|petra|katarina|katharina|lena|lisa|julia|sofia|sophie|agnieszka|monika|ewa|zofia|elena|ilona|irena|martina|lucie|tereza|alice|charlotte|lily|sarah|laura)$/i;

export function lookOf(key: string, s?: Speaker): PersonaLook {
  if (key === "buyer") return PARLEY_LOOK;
  if (!s || s.kind === "narrator") return NEUTRAL_LOOK;
  if (s.style === "custom") return { ...NEUTRAL_LOOK, key };
  const first = (s.contact ?? s.label ?? "").split(/[\s·]/)[0];
  const gender = first ? (FEMALE.test(first) ? "F" : "M") : "X";
  return lookFor(s.id ?? key, roleOf(s.role), gender, s.country);
}

/** The shared portrait set (/replays/portraits/<id>.svg); the drawn avatar only when no portrait exists. */
function SupplierPortrait({ id, speaker, size, speaking, dimmed }: { id: string; speaker: Speaker; size: number; speaking: boolean; dimmed: boolean }) {
  const [failed, setFailed] = useState(false);
  const sid = speaker.id ?? id;
  if (failed || !sid.startsWith("sup:") || speaker.style === "custom")
    return <PersonaAvatar look={lookOf(id, speaker)} size={size} speaking={speaking} dimmed={dimmed} title={speaker.label} />;
  return (
    <div className="relative inline-block" style={{ width: size, height: size }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/replays/portraits/${sid.slice(4)}.svg`}
        alt={speaker.label}
        width={size}
        height={size}
        onError={() => setFailed(true)}
        className={`block border-2 border-border bg-card transition-[filter,opacity] duration-300 ${dimmed ? "opacity-55 grayscale" : ""}`}
      />
      <svg viewBox="0 0 240 240" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
        <Mouth speaking={speaking} />
      </svg>
      {speaking ? (
        <>
          <span className="pointer-events-none absolute -inset-1 border-2 border-primary persona-ring" />
          <svg viewBox="0 0 40 30" className="persona-voice absolute bottom-1 right-1 h-5 w-7" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <rect key={i} x={i * 10} y="0" width="6" height="30" fill="var(--primary)" style={{ animationDelay: `${i * 0.12}s` }} />
            ))}
          </svg>
        </>
      ) : null}
    </div>
  );
}

function money(v: unknown) {
  return typeof v === "number" ? `€${v.toFixed(2)}` : null;
}

const SHOWN = ["unit_price", "payment_days", "lead_time_days", "incoterm", "index_clause", "contract_months", "ppm_commitment", "effective_date"];

function TermCards({ sheets, dark }: { sheets: NonNullable<Turn["terms"]>; dark: boolean }) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {sheets.slice(0, 3).map((s, i) => (
        <dl
          key={i}
          className={`min-w-[8.5rem] flex-1 border-2 px-2 py-1.5 font-mono text-[10px] leading-snug ${dark ? "border-background/40" : "border-border bg-background"}`}
        >
          <dt className="mb-1 font-semibold uppercase tracking-widest opacity-70">{s.label}</dt>
          {SHOWN.filter((k) => s.terms[k] !== undefined && s.terms[k] !== null).map((k) => (
            <dd key={k} className="flex justify-between gap-2">
              <span className="opacity-70">{k.replace(/_/g, " ").replace("days", "d")}</span>
              <span className={k === "unit_price" ? "font-bold" : ""}>{fmtTerm(k, s.terms[k])}</span>
            </dd>
          ))}
        </dl>
      ))}
    </div>
  );
}

function Bubble({
  turn,
  speaker,
  side,
  live,
  details,
  to,
}: {
  turn: StageTurn;
  speaker?: Speaker;
  side: "left" | "right";
  live: boolean;
  details: boolean;
  to?: string | null;
}) {
  const sheet = turn.terms?.[0]?.terms;
  const price = sheet ? money(sheet.unit_price) : null;
  const dark = side === "left";
  return (
    <div data-live={live ? "1" : undefined} className={`bubble-in flex ${side === "left" ? "justify-start" : "justify-end"}`}>
      <div
        className={`relative max-w-[min(34rem,88%)] border-2 border-border px-4 py-3 text-sm leading-relaxed ${
          side === "left" ? "bg-foreground text-background" : "bg-card"
        } ${live ? "shadow-[6px_6px_0_var(--primary)]" : "shadow-none"}`}
      >
        <span
          className={`absolute top-4 h-3 w-3 rotate-45 border-border ${
            side === "left" ? "-left-[7px] border-b-2 border-l-2 bg-foreground" : "-right-[7px] border-r-2 border-t-2 bg-card"
          }`}
        />
        <p className="mb-1 flex flex-wrap items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-widest opacity-70">
          <span>{speaker?.label ?? turn.speaker}</span>
          {to ? <span>→ {to}</span> : null}
          {turn.round != null ? <span>· round {turn.round}</span> : null}
          {turn.action === "accept" ? <span className="bg-primary px-1 text-primary-foreground">accepted</span> : null}
          {turn.status && turn.status !== "open" ? <span className="bg-primary px-1 text-primary-foreground">{turn.status.replace("_", " ")}</span> : null}
          {price && !details ? <span className="ml-auto">{price}</span> : null}
        </p>
        <p className="break-words">{turn.text}</p>
        {details && turn.terms?.length ? <TermCards sheets={turn.terms} dark={dark} /> : null}
        {details && turn.reason ? (
          <details className="mt-2 font-mono text-[11px]">
            <summary className="cursor-pointer font-semibold uppercase tracking-widest text-primary">Why?</summary>
            <p className="mt-1 normal-case opacity-85">{turn.reason}</p>
          </details>
        ) : null}
      </div>
    </div>
  );
}

export function Stage({
  speakers,
  turns,
  active,
  title,
  details = false,
}: {
  speakers: Record<string, Speaker>;
  turns: StageTurn[];
  active: number | null;
  title?: string | null;
  details?: boolean;
}) {
  const feed = useRef<HTMLDivElement>(null);
  const suppliers = Object.entries(speakers).filter(([, s]) => s.kind === "supplier");
  const current = active == null ? null : turns.find((t) => t.index === active);
  const talking = current?.speaker ?? null;
  const listening = talking === "buyer" ? (current?.supplier ?? null) : null;
  const short = (k?: string | null) => (k ? (speakers[k]?.contact ?? speakers[k]?.company ?? k) : null);

  useEffect(() => {
    const el = feed.current;
    if (!el) return;
    const live = el.querySelector<HTMLElement>("[data-live='1']");
    requestAnimationFrame(() => {
      if (live) live.scrollIntoView({ block: "nearest", behavior: "smooth" });
      else el.scrollTop = el.scrollHeight;
    });
  }, [turns.length, active]);

  return (
    <section data-stage className="grid gap-4 border-2 border-border bg-background p-3 sm:grid-cols-[9rem_1fr_9rem] sm:p-4">
      <div className="flex items-center gap-3 sm:flex-col sm:items-start">
        <PersonaAvatar look={PARLEY_LOOK} size={96} speaking={talking === "buyer"} title="Parley" />
        <div className="font-mono text-[10px] font-semibold uppercase tracking-widest">
          <p>Parley</p>
          <p className="text-muted-foreground">buyer agent</p>
        </div>
      </div>
      <div ref={feed} className="order-3 flex h-[26rem] flex-col sm:order-none gap-3 overflow-y-auto border-x-0 border-border px-1 sm:h-[30rem] sm:border-x-2 sm:px-4">
        {title ? <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{title}</p> : null}
        {turns.length === 0 ? <p className="m-auto font-mono text-xs uppercase tracking-widest text-muted-foreground">Waiting for the first e-mail…</p> : null}
        {turns
          .filter((t) => t.kind === "turn")
          .map((t) => (
            <Bubble
              key={t.index}
              turn={t}
              speaker={speakers[t.speaker]}
              side={t.speaker === "buyer" ? "left" : "right"}
              live={t.index === active}
              details={details}
              to={t.speaker === "buyer" && suppliers.length > 1 ? short(t.supplier) : null}
            />
          ))}
      </div>
      <div className="order-2 flex flex-wrap gap-3 sm:order-none sm:flex-col sm:flex-nowrap">
        {suppliers.map(([key, s]) => {
          const lost = turns.some((t) => t.speaker === key && (t.status === "walked_away" || t.status === "no_bid"));
          return (
            <div key={key} className={`flex w-[5.5rem] flex-col items-start gap-1 sm:w-auto ${listening === key ? "outline-2 outline-offset-4 outline-dashed outline-primary" : ""}`}>
              <SupplierPortrait id={key} speaker={s} size={suppliers.length > 2 ? 84 : 96} speaking={talking === key} dimmed={lost} />
              <div className="w-full font-mono text-[10px] font-semibold uppercase tracking-widest">
                <p className="truncate sm:max-w-[9rem]">{s.contact ?? s.label}</p>
                <p className="truncate text-muted-foreground sm:max-w-[9rem]">{s.company ?? s.role}</p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
