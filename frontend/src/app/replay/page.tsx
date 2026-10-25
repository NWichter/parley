import type { Metadata } from "next";
import { Suspense } from "react";
import { Page } from "@/components/site";
import index from "../../../public/replays/index.json";
import { ReplayBrowser } from "./browser";
import type { Collections, Entry } from "./types";

export const metadata: Metadata = {
  title: "Replays · Parley",
  description: "Parley's best negotiation per arena case as an animated thread: every e-mail a speech bubble, the term sheets and the agent's reason per move.",
};

export default function ReplayPage() {
  const entries: Entry[] = (index.sessions as Entry[]).map((e) => ({ ...e, status: e.duration ? "ready" : "text" }));
  const collections = ((index as { collections?: Collections }).collections ?? {}) as Collections;
  return (
    <Page>
      <Suspense fallback={<p className="font-mono text-sm">Loading…</p>}>
        <ReplayBrowser fallback={{ entries, replays: {}, collections }} />
      </Suspense>
    </Page>
  );
}
