import { existsSync } from "node:fs";
import { join } from "node:path";
import { Kicker } from "@/components/site";

type Scene = { title: string; text: string };

export function VideoSection({ file, scenes }: { file: string; scenes: Scene[] }) {
  const ready = existsSync(join(process.cwd(), "public", "videos", file));
  const posterFile = file.replace(/\.mp4$/, "-poster.png");
  const poster = existsSync(join(process.cwd(), "public", "videos", posterFile)) ? `/videos/${posterFile}` : undefined;
  return (
    <>
      {ready ? (
        <video
          controls
          preload="metadata"
          src={`/videos/${file}`}
          poster={poster}
          className="aspect-video w-full border-2 border-border bg-foreground"
        />
      ) : (
        <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 border-2 border-dashed border-primary bg-card p-6 text-center">
          <span className="font-heading text-4xl font-bold uppercase">In production</span>
          <span className="font-mono text-sm text-muted-foreground">The video goes online before the code freeze on Sunday, 13:00.</span>
        </div>
      )}
      <section className="flex flex-col gap-4">
        <Kicker>Scenes</Kicker>
        <ol className="grid gap-3 sm:grid-cols-2">
          {scenes.map((s, i) => (
            <li key={s.title} className="flex gap-4 border-2 border-border bg-card p-4">
              <span className="font-mono text-sm font-bold text-signal-ink">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <p className="font-heading text-xl font-bold uppercase">{s.title}</p>
                <p className="text-sm leading-relaxed text-foreground/80">{s.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
