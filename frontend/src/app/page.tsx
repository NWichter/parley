import { HealthBadge } from "@/components/health-badge";
import { Logo } from "@/components/logo";
import { SiteFooter, SiteNav } from "@/components/site";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const steps = [
  {
    num: "01",
    label: "Prepare",
    title: "Read the supplier",
    text: "Before the first e-mail, Parley builds a dossier from the company data: what the supplier needs, what it fears, and which facts can be checked.",
  },
  {
    num: "02",
    label: "Negotiate",
    title: "Trade terms, not just price",
    text: "Each round sends term sheets that cost us about the same but differ in what the supplier values: payment days, contract length, quality terms.",
  },
  {
    num: "03",
    label: "Close",
    title: "Sign or walk",
    text: "It accepts the cheapest standing offer that keeps every must-have, or walks away when our alternative is cheaper.",
  },
];

function Kicker({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
      <span className="h-2 w-5 bg-primary" />
      {children}
    </p>
  );
}

export default function Home() {
  return (
    <main className="w-full flex-1 overflow-x-hidden bg-[linear-gradient(var(--grid)_1px,transparent_1px),linear-gradient(90deg,var(--grid)_1px,transparent_1px)] bg-[size:32px_32px]">
      <div className="mx-auto flex max-w-5xl flex-col gap-16 px-4 py-16 sm:px-8 sm:py-24">
        <header className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Logo />
            <div className="flex flex-wrap items-center gap-5">
              <SiteNav />
              <HealthBadge />
            </div>
          </div>
          <Kicker>Industrial Agents Hackathon · Tacto track · Team Clooless</Kicker>
          <h1 className="font-heading text-5xl font-bold uppercase leading-[0.92] break-words sm:text-8xl">
            Parley.
            <br />
            <span className="text-primary">The AI buyer that negotiates.</span>
          </h1>
          <p className="max-w-2xl text-xl leading-relaxed text-foreground/80">
            Parley negotiates with industrial suppliers by e-mail, on its own. It goes for the lowest total
            cost over the contract, not just the lowest unit price, and it can explain every trade it makes.
          </p>
          <p className="max-w-2xl border-l-4 border-primary pl-4 font-mono text-sm leading-relaxed">
            Our practice runs: mean score 0.792 on the ten practice cases, no zeros, ahead of Tacto&apos;s house agent
            (0.70) on 7 of 10.
          </p>
          <div className="flex flex-wrap gap-3">
            <a href="/pitch/" className={buttonVariants({ size: "lg" })}>
              Open the pitch deck
            </a>
            <a href="#how" className={cn(buttonVariants({ variant: "outline", size: "lg" }), "border-2 border-foreground")}>
              How it works
            </a>
          </div>
        </header>

        <section className="grid gap-8 border-2 border-border bg-card p-6 sm:grid-cols-2 sm:p-10">
          <div className="flex flex-col gap-3">
            <Kicker>The challenge</Kicker>
            <p className="text-lg leading-relaxed">
              In the Tacto Negotiation Arena, Parley buys parts for Hollerau Pumpenwerke, a fictional German
              pump maker. Each case starts with a supplier e-mail: a price increase, an expiring contract, a
              request for quotes. Suppliers are agents with their own costs and limits. No human steps in.
            </p>
          </div>
          <div className="flex flex-col gap-3">
            <Kicker>Our approach</Kicker>
            <p className="text-lg leading-relaxed">
              Code owns every number: the total cost of each offer, the term sheets, when to accept or walk.
              Templates write the e-mails; the final runs without a language model, because measured on paired
              games it did not move the score. Every decision is logged with its reason, so each deal can be
              replayed and checked.
            </p>
          </div>
        </section>

        <section id="how" className="flex flex-col gap-6">
          <Kicker>How it works</Kicker>
          <ol className="grid gap-4 sm:grid-cols-3">
            {steps.map((s, i) => (
              <li
                key={s.num}
                className={`flex flex-col gap-3 border-2 bg-card p-6 ${
                  i === 1 ? "border-primary shadow-[8px_8px_0_var(--accent)]" : "border-border"
                }`}
              >
                <span className="font-mono text-sm font-bold uppercase tracking-widest text-signal-ink">
                  {s.num} · {s.label}
                </span>
                <h2 className="font-heading text-3xl font-bold uppercase">{s.title}</h2>
                <p className="leading-relaxed text-foreground/80">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <SiteFooter />
      </div>
    </main>
  );
}
