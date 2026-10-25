import "./demo-hero.css";

const steps = "M242 81H252V121H312V170H372V219H432V267H482";

function Corners({ x, y, s, arm }: { x: number; y: number; s: number; arm: number }) {
  const h = s / 2;
  return (
    <g fill="none" stroke="#f2541b" strokeWidth="5" strokeLinecap="square">
      <path d={`M${x - h} ${y - h + arm}V${y - h}H${x - h + arm}`} />
      <path d={`M${x + h - arm} ${y - h}H${x + h}V${y - h + arm}`} />
      <path d={`M${x - h} ${y + h - arm}V${y + h}H${x - h + arm}`} />
      <path d={`M${x + h - arm} ${y + h}H${x + h}V${y + h - arm}`} />
    </g>
  );
}

export function DemoHero() {
  return (
    <figure className="demo-hero grid gap-4 sm:grid-cols-[4fr_5fr]" aria-label="A supplier asks for +9.5 %, Parley's offers walk the price down to the target">
      <svg viewBox="0 0 400 360" className="dh-card w-full border-2 border-border bg-card" aria-hidden="true">
        <rect width="400" height="36" fill="#16191d" />
        <text x="18" y="23" className="font-mono" fontSize="13" fontWeight="700" letterSpacing="2" fill="#fbfaf7">
          INBOX · NEW
        </text>
        <text x="382" y="23" className="font-mono" fontSize="13" fontWeight="700" letterSpacing="2" fill="#f2541b" textAnchor="end">
          ROUND 0
        </text>
        <g className="font-mono" fontSize="14" fill="#16191d">
          <text x="18" y="66" fill="#69727d">FROM</text>
          <text x="104" y="66">Foundry · key account</text>
          <line x1="18" x2="382" y1="78" y2="78" stroke="#d3d1c8" strokeWidth="2" />
          <text x="18" y="102" fill="#69727d">SUBJECT</text>
          <text x="104" y="102">Price adjustment</text>
          <line x1="18" x2="382" y1="114" y2="114" stroke="#d3d1c8" strokeWidth="2" />
        </g>
        <rect className="dh-bar" x="18" y="132" width="320" height="9" fill="#d3d1c8" />
        <rect className="dh-bar" x="18" y="150" width="270" height="9" fill="#d3d1c8" style={{ animationDelay: "0.15s" }} />
        <text x="18" y="196" className="font-mono" fontSize="12" fontWeight="600" letterSpacing="2" fill="#69727d">
          REQUESTED
        </text>
        <text x="12" y="276" className="dh-on font-heading" fontSize="96" fontWeight="700" fill="#f2541b">
          +9.5 %
        </text>
        <g className="font-mono" fontSize="11" fontWeight="700" letterSpacing="1">
          {["A · PAYMENT", "B · CONTRACT", "C · PRICE"].map((t, i) => (
            <g key={t} className={`dh-chip-${i}`}>
              <rect x={18 + i * 122} y="310" width="114" height="28" fill="#16191d" />
              <text x={26 + i * 122} y="328" fill="#fbfaf7">
                {t}
              </text>
            </g>
          ))}
        </g>
      </svg>
      <svg viewBox="0 0 520 360" className="dh-card w-full border-2 border-border bg-card" aria-hidden="true">
        {[81, 121, 170, 219].map((y) => (
          <line key={y} x1="56" x2="500" y1={y} y2={y} stroke="#e6e4dc" strokeWidth="1.5" />
        ))}
        <path d="M56 22V316H500" fill="none" stroke="#16191d" strokeWidth="3" />
        <text x="56" y="16" className="font-mono" fontSize="11" fontWeight="600" letterSpacing="2" fill="#69727d">
          PRICE INDEX
        </text>
        <text x="500" y="342" className="font-mono" fontSize="11" fontWeight="600" letterSpacing="2" fill="#69727d" textAnchor="end">
          ROUNDS →
        </text>
        <text x="46" y="239" className="font-mono" fontSize="11" fill="#69727d" textAnchor="end">
          100
        </text>
        <line x1="56" x2="500" y1="283" y2="283" stroke="#f2541b" strokeWidth="2.5" strokeDasharray="9 7" />
        <text x="500" y="302" className="font-mono" fontSize="11" fontWeight="700" letterSpacing="2" fill="#c23d0c" textAnchor="end">
          TARGET
        </text>
        <path className="dh-ask" d="M56 235H154V81H242" pathLength={1} fill="none" stroke="#16191d" strokeWidth="4.5" />
        <text className="dh-ask-label font-heading" x="160" y="68" fontSize="22" fontWeight="700" fill="#16191d">
          +9.5 %
        </text>
        <path className="dh-path" d={steps} pathLength={1} fill="none" stroke="#f2541b" strokeWidth="4.5" />
        <rect className="dh-dot" x="475" y="260" width="14" height="14" fill="#f2541b" />
        <g className="dh-finder">
          <Corners x={482} y={267} s={58} arm={16} />
        </g>
      </svg>
    </figure>
  );
}
