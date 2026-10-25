"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Stage, type StageTurn } from "@/components/negotiation/stage";
import type { Replay } from "./types";

const btn =
  "border-2 border-border px-3 py-1.5 font-mono text-xs font-semibold uppercase tracking-widest disabled:opacity-40 hover:enabled:text-primary";

function readSeconds(text: string) {
  return Math.min(8, Math.max(1.6, text.split(/\s+/).length / 3.2));
}

/** The negotiation as an animated thread: the whole thread is shown first (static fallback, also for reduced motion);
 * Play reveals it e-mail by e-mail, in sync with the voices when the replay has audio. */
export function BubblePlayer({ data, only, start }: { data: Replay; only?: string | null; start?: number | null }) {
  const turns = useMemo<StageTurn[]>(
    () =>
      data.turns
        .map((t, index) => ({ ...t, index }))
        .filter((t) => t.kind === "turn" && (!only || t.speaker === only || (t.speaker === "buyer" && t.supplier === only))),
    [data, only],
  );
  const speakers = useMemo(
    () => (only ? Object.fromEntries(Object.entries(data.speakers).filter(([k, s]) => s.kind !== "supplier" || k === only)) : data.speakers),
    [data, only],
  );
  const voiced = Boolean(data.audio) && turns.some((t) => t.start != null);
  const [shown, setShown] = useState(start != null ? Math.min(start, turns.length) : turns.length);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const audio = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!playing || voiced || shown >= turns.length) return;
    const t = setTimeout(() => {
      setShown(shown + 1);
      if (shown + 1 >= turns.length) setPlaying(false);
    }, shown === 0 ? 250 : (readSeconds(turns[shown - 1]?.text ?? "") * 1000) / speed);
    return () => clearTimeout(t);
  }, [playing, shown, turns, speed, voiced]);

  function play() {
    if (shown >= turns.length) setShown(0);
    setPlaying(true);
    if (voiced && audio.current) {
      if (shown >= turns.length) audio.current.currentTime = 0;
      void audio.current.play();
    }
  }

  function pause() {
    setPlaying(false);
    audio.current?.pause();
  }

  function step(d: number) {
    pause();
    setShown((n) => {
      const next = Math.min(turns.length, Math.max(1, n + d));
      const t = turns[next - 1];
      if (voiced && audio.current && t?.start != null) audio.current.currentTime = t.start;
      return next;
    });
  }

  function onTime(time: number) {
    if (!voiced) return;
    let n = 0;
    turns.forEach((t, i) => {
      if ((t.start ?? Infinity) <= time + 0.05) n = i + 1;
    });
    setShown(Math.max(1, n));
  }

  const visible = turns.slice(0, shown);
  const active = playing || shown < turns.length ? (visible.at(-1)?.index ?? null) : null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {playing ? (
          <button type="button" className={`${btn} bg-foreground text-background`} onClick={pause}>
            ❚❚ Pause
          </button>
        ) : (
          <button type="button" className={`${btn} bg-primary text-primary-foreground`} onClick={play}>
            ▶ {shown >= turns.length ? "Play from the start" : "Play"}
          </button>
        )}
        <button type="button" className={btn} onClick={() => step(-1)} disabled={shown <= 1}>
          ◀ Step
        </button>
        <button type="button" className={btn} onClick={() => step(1)} disabled={shown >= turns.length}>
          Step ▶
        </button>
        <button
          type="button"
          className={btn}
          onClick={() => {
            pause();
            setShown(turns.length);
          }}
          disabled={shown >= turns.length && !playing}
        >
          Show all
        </button>
        {!voiced ? (
          <button type="button" className={btn} onClick={() => setSpeed((s) => (s === 1 ? 2 : s === 2 ? 4 : 1))}>
            {speed}×
          </button>
        ) : null}
        <span className="ml-auto font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          {Math.min(shown, turns.length)} / {turns.length} e-mails
        </span>
      </div>
      {voiced ? (
        <audio
          ref={audio}
          controls
          preload="metadata"
          src={data.audio}
          className="h-10 w-full"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
          onSeeked={(e) => onTime(e.currentTarget.currentTime)}
        />
      ) : null}
      <Stage speakers={speakers} turns={visible} active={active} details />
    </section>
  );
}
