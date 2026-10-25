import type { Metadata } from "next";
import { DemoHero } from "./demo-hero";
import { Page, Title } from "@/components/site";
import { VideoSection } from "@/components/video-section";
import { Walkthrough } from "./walkthrough";

export const metadata: Metadata = { title: "Demo · Parley" };

const scenes = [
  { title: "The case", text: "Practice case P11: a foundry quotes €47.00 a part at 6,400 defects per million." },
  { title: "The supplier", text: "Marek Lis, export manager of a Polish foundry: cash pressure, resists margin cuts, each read from Tacto's data with its source." },
  { title: "Negotiation", text: "Every offer priced as one total cost; packages of nearly equal cost to us, the supplier picks." },
  { title: "Deal", text: "Six rounds: €45.50 at 300 ppm, total cost −8 %, mostly from quality. This run: 0.71 (best on P11: 0.79); Tacto's house agent: 0.31." },
  { title: "Results", text: "Ten practice cases without a model, as in the final (v1.36): mean 0.79, no zeros, ahead of Tacto's house agent (0.70) on 7 of 10." },
  { title: "Outro", text: "Every decision is in the replay." },
];

export default function DemoPage() {
  return (
    <Page>
      <Title kicker="Submission · 1-minute demo" title="Parley" accent="in action.">
        One negotiation from the first supplier e-mail to the signed deal, recorded from the real agent
        against the Tacto Negotiation Arena.
      </Title>
      <VideoSection file="demo.mp4" scenes={scenes} />
      <DemoHero />
      <Walkthrough />
    </Page>
  );
}
