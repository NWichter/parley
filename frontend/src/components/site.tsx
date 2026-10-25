import Link from "next/link";
import type { ReactNode } from "react";
import { Logo } from "@/components/logo";

const links = [
  { href: "/demo", label: "Demo" },
  { href: "/tech", label: "Tech" },
  { href: "/architecture", label: "Architecture" },
  { href: "/replay", label: "Replay" },
  { href: "/pitch/", label: "Pitch" },
];

export function Kicker({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
      <span className="h-2 w-5 bg-primary" />
      {children}
    </p>
  );
}

export function SiteNav() {
  return (
    <nav className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-xs font-semibold uppercase tracking-widest">
      {links.map((l) =>
        l.href.startsWith("/pitch") ? (
          <a key={l.href} href={l.href} className="hover:text-primary">
            {l.label}
          </a>
        ) : (
          <Link key={l.href} href={l.href} className="hover:text-primary">
            {l.label}
          </Link>
        ),
      )}
    </nav>
  );
}

export function SiteFooter() {
  return (
    <footer className="flex flex-wrap items-center justify-between gap-3 border-t-2 border-border pt-6 font-mono text-xs uppercase tracking-widest text-muted-foreground">
      <span>Industrial Agents Hackathon · Munich · 24–25 Oct 2026</span>
      <span>Team Clooless</span>
      <span className="w-full normal-case tracking-normal">
        Case and supplier data: Tacto Negotiation Arena, Tacto Technology GmbH, licensed under{" "}
        <a className="underline" href="https://creativecommons.org/licenses/by/4.0/" rel="license noreferrer" target="_blank">
          CC BY 4.0
        </a>
        ; shown in condensed and derived form.
      </span>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <main className="w-full flex-1 overflow-x-hidden bg-[linear-gradient(var(--grid)_1px,transparent_1px),linear-gradient(90deg,var(--grid)_1px,transparent_1px)] bg-[size:32px_32px]">
      <div className="mx-auto flex max-w-5xl flex-col gap-14 px-4 py-12 sm:px-8 sm:py-16">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <Logo />
          <SiteNav />
        </header>
        {children}
        <SiteFooter />
      </div>
    </main>
  );
}

export function Title({ kicker, title, accent, children }: { kicker: string; title: string; accent: string; children?: ReactNode }) {
  return (
    <section className="flex flex-col gap-5">
      <Kicker>{kicker}</Kicker>
      <h1 className="font-heading text-5xl font-bold uppercase leading-[0.92] break-words sm:text-7xl">
        {title}
        <br />
        <span className="text-primary">{accent}</span>
      </h1>
      {children && <div className="max-w-3xl text-lg leading-relaxed text-foreground/80">{children}</div>}
    </section>
  );
}
