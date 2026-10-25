import type { Metadata } from "next";
import Link from "next/link";
import { Page, Title } from "@/components/site";
import { VideoSection } from "@/components/video-section";

export const metadata: Metadata = { title: "Technical explanation · Parley" };

const scenes = [
  { title: "Architecture", text: "Deterministic: dossier, valuation, policy, e-mail templates, runner. Code owns every number." },
  { title: "Valuation", text: "Every offer becomes one total cost; packages of nearly equal cost trade price against one other term." },
  { title: "Facts", text: "Only with a data ID the supplier checks against Tacto's data; supplier profiles from the same data." },
  { title: "Model on vs off", text: "90 paired games: no measurable gain from the model (+0.0001). The final runs on rules and templates." },
  { title: "Tests", text: "Our own tests: 180 persona-bank games, suppliers also voiced by several model families; all ten practice cases live; a fault-injection rehearsal of the final." },
  { title: "Outro", text: "Parley, built by Team Clooless." },
];

export default function TechPage() {
  return (
    <Page>
      <Title kicker="Submission · 1-minute technical explanation" title="How Parley" accent="works.">
        The architecture and the tech stack in one minute. The written notes are on the{" "}
        <Link href="/architecture" className="underline decoration-primary decoration-2 underline-offset-4">
          architecture page
        </Link>
        .
      </Title>
      <VideoSection file="tech.mp4" scenes={scenes} />
    </Page>
  );
}
