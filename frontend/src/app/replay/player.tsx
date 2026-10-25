"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Replay, Sheet, Terms, Turn } from "./types";

const termLabel: Record<string, string> = {
  unit_price: "Price",
  payment_days: "Payment",
  lead_time_days: "Lead time",
  volume_per_year: "Volume",
  contract_months: "Contract",
  effective_date: "From",
  incoterm: "Incoterm",
  index_clause: "Index",
  tooling_eur: "Tooling",
  ppm_commitment: "Quality",
  chargeback_eur_per_complaint: "Chargeback",
};

export function fmtTerm(key: string, v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (key === "unit_price" && typeof v === "number") return `€${v.toFixed(2)}`;
  if (key === "payment_days" || key === "lead_time_days") return `${v} days`;
  if (key === "contract_months") return `${v} months`;
  if (key === "volume_per_year" && typeof v === "number") return `${v.toLocaleString("en-US")} / yr`;
  if (key === "ppm_commitment" && typeof v === "number") return `${v.toLocaleString("en-US")} ppm`;
  if (key === "tooling_eur" && typeof v === "number") return v ? `€${v.toLocaleString("en-US")}` : "none";
  if (key === "chargeback_eur_per_complaint" && typeof v === "number") return v ? `€${v} / complaint` : "none";
  if (key === "index_clause" && typeof v === "object") {
    const c = v as { series?: string; material_share?: number; deadband_pct?: number };
    return [c.series, c.material_share !== undefined ? `${Math.round(c.material_share * 100)}% share` : null, c.deadband_pct !== undefined ? `±${c.deadband_pct}%` : null]
      .filter(Boolean)
      .join(", ");
  }
  return String(v);
}

function TermSheet({ sheet }: { sheet: Sheet }) {
  const rows = Object.entries(sheet.terms as Terms).filter(([k]) => termLabel[k]);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{sheet.label}</span>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 font-mono text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{termLabel[k]}</dt>
            <dd className={`break-words ${k === "unit_price" ? "font-bold text-signal-ink" : ""}`}>{fmtTerm(k, v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const SUPPLIER_COLORS = ["#16191d", "#2f6f8f", "#5b7f3a", "#7a4f9a"];

export function speakerColors(r: Replay): Record<string, string> {
  const out: Record<string, string> = { narrator: "var(--muted-foreground)", buyer: "var(--primary)" };
  Object.entries(r.speakers)
    .filter(([, s]) => s.kind === "supplier")
    .forEach(([k], i) => (out[k] = SUPPLIER_COLORS[i % SUPPLIER_COLORS.length]));
  return out;
}

const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function Portrait({ id, name, color }: { id?: string | null; name: string; color: string }) {
  const [failed, setFailed] = useState(false);
  const src = id?.startsWith("sup:") && !failed ? `/replays/portraits/${id.slice(4)}.svg` : null;
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  return (
    <span className="relative h-16 w-16 shrink-0 border-2 border-border bg-background">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" width={64} height={64} className="h-full w-full" onError={() => setFailed(true)} />
      ) : (
        <span className="flex h-full w-full items-center justify-center font-heading text-2xl font-bold uppercase" style={{ color }}>
          {initials || "?"}
        </span>
      )}
      <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 border-2 border-card" style={{ background: color }} />
    </span>
  );
}

export function Cast({ data }: { data: Replay }) {
  const colors = speakerColors(data);
  return (
    <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(data.speakers).map(([k, s]) => (
        <li key={k} className="flex min-w-0 gap-3 border-2 border-border bg-card p-3">
          <Portrait
            id={s.kind === "supplier" ? (s.id ?? (k.startsWith("sup:") ? k : null)) : null}
            name={s.kind === "supplier" ? (s.contact ?? s.company ?? "S") : s.kind === "buyer" ? "P" : "N"}
            color={colors[k]}
          />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              {s.kind === "supplier" ? (s.company ?? "Supplier") : s.kind === "buyer" ? "Buyer · our agent" : "Narrator"}
            </span>
            <span className="text-sm font-semibold leading-snug">{s.kind === "supplier" ? s.label : s.kind === "buyer" ? "Parley" : "Narration"}</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              voice {s.voice}
              {s.style ? ` · ${s.style}` : ""}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ReplayPlayer({ data }: { data: Replay }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [time, setTime] = useState(0);
  const colors = useMemo(() => speakerColors(data), [data]);
  const turns = data.turns;
  const active = turns.findIndex((t) => t.start !== null && t.end !== null && time >= t.start && time < t.end);
  const multi = Object.values(data.speakers).filter((s) => s.kind === "supplier").length > 1;

  useEffect(() => {
    const el = audio.current;
    if (!el) return;
    const tick = () => setTime(el.currentTime);
    el.addEventListener("timeupdate", tick);
    el.addEventListener("seeked", tick);
    return () => {
      el.removeEventListener("timeupdate", tick);
      el.removeEventListener("seeked", tick);
    };
  }, [data.audio]);

  function seek(t: Turn) {
    const el = audio.current;
    if (!el || t.start === null) return;
    el.currentTime = t.start + 0.01;
    void el.play();
  }

  function who(t: Turn) {
    const s = data.speakers[t.speaker];
    if (t.speaker === "buyer") {
      const to = t.supplier && multi ? data.speakers[t.supplier]?.company : null;
      return `Parley${to ? ` → ${to.split(" ").slice(0, 2).join(" ")}` : ""}`;
    }
    if (t.speaker === "narrator") return "Narrator";
    return s?.contact ? `${s.contact}${s.company ? ` · ${s.company.split(" ").slice(0, 2).join(" ")}` : ""}` : (s?.label ?? t.speaker);
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="sticky top-0 z-10 flex flex-col gap-2 border-2 border-border bg-card p-3 sm:p-4">
        <audio ref={audio} controls preload="metadata" src={data.audio} className="w-full" />
        {active >= 0 && (
          <p className="truncate font-mono text-[11px] uppercase tracking-widest" style={{ color: colors[turns[active].speaker] }}>
            ▶ {who(turns[active])}
          </p>
        )}
      </div>
      <ol className="flex flex-col gap-3">
        {turns.map((t, i) => {
          const isActive = i === active;
          const narration = t.kind === "narration";
          const buyer = t.speaker === "buyer";
          return (
            <li
              key={i}
              className={`grid min-w-0 gap-4 border-2 bg-card p-4 transition-colors md:grid-cols-[1fr_230px] ${
                isActive ? "border-primary shadow-[6px_6px_0_var(--accent)]" : "border-border"
              } ${narration ? "bg-muted/40" : ""} ${buyer ? "md:ml-10" : narration ? "" : "md:mr-10"}`}
              style={{ borderLeftColor: colors[t.speaker], borderLeftWidth: 6 }}
            >
              <div className="flex min-w-0 flex-col gap-2">
                <button
                  type="button"
                  onClick={() => seek(t)}
                  disabled={t.start === null}
                  className="flex flex-col gap-2 text-left disabled:cursor-default"
                >
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] font-semibold uppercase tracking-widest">
                    <span style={{ color: colors[t.speaker] }} className={buyer ? "text-signal-ink" : ""}>
                      {who(t)}
                    </span>
                    {t.round !== undefined && t.kind === "turn" && (
                      <span className="text-muted-foreground">{t.round === 0 ? "opening" : `round ${t.round}`}</span>
                    )}
                    {t.start !== null && <span className="text-muted-foreground">{clock(t.start)}</span>}
                    {t.spoken === false ? (
                      <span className="text-muted-foreground">· not read aloud</span>
                    ) : t.placeholder ? (
                      <span className="text-muted-foreground">· {t.action}</span>
                    ) : (
                      t.condensed && <span className="text-muted-foreground">· condensed</span>
                    )}
                    {t.status && !buyer && !narration && t.status !== "open" && (
                      <span className="bg-foreground px-1.5 py-0.5 text-background">{t.status.replace("_", " ")}</span>
                    )}
                  </span>
                  <span
                    className={`leading-relaxed ${narration ? "italic text-foreground/80" : ""} ${t.spoken === false ? "text-muted-foreground" : ""} ${
                      t.placeholder ? "italic" : ""
                    }`}
                  >
                    {t.text}
                  </span>
                </button>
                {t.reason && (
                  <p className="flex flex-wrap items-baseline gap-2 border-l-4 border-foreground bg-background px-3 py-1.5 font-mono text-[11px] leading-relaxed">
                    <span className="bg-foreground px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-background">reason</span>
                    <span className="min-w-0 break-words">{t.reason}</span>
                  </p>
                )}
                {t.full && t.condensed && (
                  <details className="group">
                    <summary className="cursor-pointer font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground hover:text-primary">
                      Full e-mail
                    </summary>
                    <pre className="mt-2 whitespace-pre-wrap break-words border-l-4 border-border bg-background px-3 py-2 font-sans text-sm leading-relaxed">
                      {t.full}
                    </pre>
                  </details>
                )}
              </div>
              {t.terms && t.terms.length > 0 && (
                <div className="flex min-w-0 flex-col gap-3 border-t-2 border-border pt-3 md:border-t-0 md:border-l-2 md:pt-0 md:pl-4">
                  {t.terms.map((s, j) => (
                    <TermSheet key={j} sheet={{ ...s, label: s.label === "package" && t.terms!.length > 1 ? `package ${j + 1}` : s.label }} />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
