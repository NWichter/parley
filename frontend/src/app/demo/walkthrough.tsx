import { existsSync } from "node:fs";
import { join } from "node:path";
import { Kicker } from "@/components/site";

export const WALKTHROUGH_URL = "";

function embedUrl(url: string) {
  const m = url.match(/(?:youtu\.be\/|v=|embed\/|shorts\/)([\w-]{11})/);
  return m ? `https://www.youtube-nocookie.com/embed/${m[1]}` : null;
}

export function Walkthrough() {
  const embed = WALKTHROUGH_URL ? embedUrl(WALKTHROUGH_URL) : null;
  const local = existsSync(join(process.cwd(), "public", "videos", "walkthrough.mp4"));
  const poster = existsSync(join(process.cwd(), "public", "videos", "walkthrough-poster.png")) ? "/videos/walkthrough-poster.png" : undefined;
  return (
    <section className="flex flex-col gap-4">
      <Kicker>2-minute walkthrough</Kicker>
      {embed ? (
        <iframe
          src={embed}
          title="Parley: 2-minute walkthrough"
          allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          className="aspect-video w-full border-2 border-border bg-foreground"
        />
      ) : WALKTHROUGH_URL ? (
        <a href={WALKTHROUGH_URL} target="_blank" rel="noreferrer" className="border-2 border-border bg-card p-5 font-heading text-2xl font-bold uppercase hover:text-primary">
          Watch the 2-minute walkthrough
        </a>
      ) : local ? (
        <video controls preload="metadata" src="/videos/walkthrough.mp4" poster={poster} className="aspect-video w-full border-2 border-border bg-foreground" />
      ) : (
        <p className="border-2 border-dashed border-primary bg-card p-5 font-mono text-sm text-muted-foreground">
          Coming: a 2-minute walkthrough of one full negotiation.
        </p>
      )}
    </section>
  );
}
