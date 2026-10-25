import Link from "next/link";

export function Mark({ className = "size-10", animated = false }: { className?: string; animated?: boolean }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" fill="#16191d" />
      <g className={animated ? "mark-lock" : undefined}>
        <path d="M13 29V13h16" fill="none" stroke="#fbfaf7" strokeWidth="6" strokeLinecap="square" />
        <path d="M51 35v16H35" fill="none" stroke="#fbfaf7" strokeWidth="6" strokeLinecap="square" />
      </g>
      <rect className={animated ? "mark-lock-sq" : undefined} x="25" y="25" width="14" height="14" fill="#f2541b" />
    </svg>
  );
}

export function Logo() {
  return (
    <Link href="/" className="flex items-center gap-3" aria-label="Parley home">
      <Mark animated />
      <span className="font-heading text-4xl font-bold leading-none">Parley</span>
    </Link>
  );
}
