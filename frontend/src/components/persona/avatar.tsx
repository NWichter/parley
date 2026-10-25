import type { ReactNode } from "react";

const INK = "#16191d";
const CARD = "#fbfaf7";
const SIGNAL = "#f2541b";
const SKIN = ["#f1d3bd", "#e5b996", "#d29b74", "#b77b52", "#8d5a3b", "#f6dccb"];
const HAIR = ["#2b2420", "#4a3426", "#6e4b31", "#a87a4f", "#c9b48f", "#8a8d91", "#d9d6cf"];
const CLOTH: Record<Role, string[]> = {
  owner: ["#3e4f5c", "#4c5a3a", "#5a4636"],
  account: ["#1f2a36", "#2e2f33", "#34404d"],
  inside: ["#6d7782", "#4f6d7a", "#7b6d62"],
  export: ["#2f3e4f", "#443a52", "#3b4a40"],
  buyer: [INK],
  neutral: ["#8a8d91"],
};

export type Role = "owner" | "account" | "inside" | "export" | "buyer" | "neutral";
export type HairStyle = "short" | "side" | "bald" | "crop" | "long" | "bob" | "bun" | "none";
export type Beard = "none" | "full" | "stubble" | "goatee";

export type PersonaLook = {
  key: string;
  role: Role;
  gender?: "M" | "F" | "X";
  country?: string | null;
  skin?: string;
  hair?: string;
  hairStyle?: HairStyle;
  beard?: Beard;
  cloth?: string;
};

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function pick<T>(key: string, seq: T[], salt = ""): T {
  return seq[hash(key + salt) % seq.length];
}

export function roleOf(role?: string | null): Role {
  const r = (role ?? "").toLowerCase();
  if (r.includes("owner") || r.includes("managing")) return "owner";
  if (r.includes("export")) return "export";
  if (r.includes("inside")) return "inside";
  return "account";
}

export function lookFor(key: string, role: Role, gender: "M" | "F" | "X" = "X", country?: string | null): PersonaLook {
  const g = gender === "X" ? pick(key, ["M", "F"] as const, "g") : gender;
  const hairStyle: HairStyle =
    g === "F" ? pick(key, ["long", "bob", "bun"] as HairStyle[], "h") : pick(key, ["short", "side", "bald", "crop"] as HairStyle[], "h");
  const beard: Beard = g === "M" ? pick(key, ["none", "none", "full", "stubble", "goatee"] as Beard[], "b") : "none";
  return {
    key,
    role,
    gender: g,
    country,
    skin: pick(key, SKIN, "s"),
    hair: pick(key, HAIR, "c"),
    hairStyle,
    beard,
    cloth: pick(key, CLOTH[role] ?? CLOTH.account, "k"),
  };
}

export const PARLEY_LOOK: PersonaLook = {
  key: "parley",
  role: "buyer",
  gender: "X",
  country: null,
  skin: "#e5b996",
  hair: INK,
  hairStyle: "crop",
  beard: "none",
  cloth: INK,
};

export const NEUTRAL_LOOK: PersonaLook = {
  key: "neutral",
  role: "neutral",
  gender: "X",
  country: null,
  skin: "#c9c6bd",
  hair: "#8a8d91",
  hairStyle: "none",
  beard: "none",
  cloth: "#8a8d91",
};

function Hair({ style, color }: { style: HairStyle; color: string }) {
  switch (style) {
    case "long":
      return <path d="M78 96c0-34 20-52 42-52s42 18 42 52v60h-16V104H94v52H78z" fill={color} />;
    case "bob":
      return <path d="M80 98c0-32 18-52 40-52s40 20 40 52v24h-14v-26H94v26H80z" fill={color} />;
    case "bun":
      return (
        <>
          <circle cx="120" cy="44" r="14" fill={color} />
          <path d="M84 96c0-30 16-48 36-48s36 18 36 48c-8-14-22-22-36-22s-28 8-36 22z" fill={color} />
        </>
      );
    case "bald":
      return <path d="M84 100c0-10 2-16 6-20h6v32h-6c-4-2-6-6-6-12zM156 100c0-10-2-16-6-20h-6v32h6c4-2 6-6 6-12z" fill={color} />;
    case "side":
      return <path d="M84 98c0-30 16-46 38-46 20 0 34 14 34 40-14-12-34-16-56-10-6 2-12 8-16 16z" fill={color} />;
    case "crop":
      return <path d="M86 92c2-26 16-40 34-40s32 14 34 40c-10-8-22-12-34-12s-24 4-34 12z" fill={color} />;
    case "short":
      return <path d="M84 96c0-28 16-44 36-44s36 16 36 44c-6-10-18-18-36-18s-30 8-36 18z" fill={color} />;
    default:
      return null;
  }
}

/** Beards are drawn fully opaque on top of the face, so they never look see-through. */
function BeardShape({ beard, color }: { beard: Beard; color: string }) {
  if (beard === "full")
    return <path d="M86 112c2 26 16 42 34 42s32-16 34-42c-6 8-12 12-18 12-4-6-10-8-16-8s-12 2-16 8c-6 0-12-4-18-12z" fill={color} />;
  if (beard === "stubble")
    return (
      <path
        d="M88 116c4 22 16 34 32 34s28-12 32-34c-6 6-12 9-18 9-4-4-9-6-14-6s-10 2-14 6c-6 0-12-3-18-9z"
        fill={color}
        stroke={color}
        strokeWidth="1"
        strokeDasharray="1 2"
      />
    );
  if (beard === "goatee") return <path d="M110 128c0 12 4 22 10 22s10-10 10-22c-3 3-6 4-10 4s-7-1-10-4z" fill={color} />;
  return null;
}

/** A simple mouth on the shared head geometry (head ellipse 120/104, 34×40): a closed line, open while speaking. */
export function Mouth({ speaking }: { speaking: boolean }) {
  return speaking ? (
    <ellipse className="persona-mouth" cx="120" cy="124" rx="8" ry="5" fill={INK} />
  ) : (
    <path d="M113 124h14" stroke={INK} strokeWidth="2.5" strokeLinecap="round" />
  );
}

function Attire({ role, cloth, gender }: { role: Role; cloth: string; gender?: string }) {
  const body = <path d="M40 240c0-44 34-74 80-74s80 30 80 74z" fill={cloth} />;
  if (role === "buyer")
    return (
      <>
        {body}
        <path d="M100 168c6 12 14 18 20 18s14-6 20-18" fill={CARD} />
        <rect x="136" y="200" width="24" height="24" fill={SIGNAL} />
        <path d="M142 206h12v6h-6v6h-6z" fill={CARD} />
        <path d="M82 108c0-30 16-48 38-48s38 18 38 48" fill="none" stroke={SIGNAL} strokeWidth="6" />
        <rect x="76" y="100" width="12" height="22" fill={SIGNAL} />
        <rect x="152" y="100" width="12" height="22" fill={SIGNAL} />
        <path d="M82 122c0 14 10 22 24 22" fill="none" stroke={SIGNAL} strokeWidth="3" />
        <rect x="104" y="140" width="8" height="6" fill={SIGNAL} />
      </>
    );
  if (role === "neutral")
    return (
      <>
        {body}
        <path d="M100 168c6 12 14 18 20 18s14-6 20-18" fill={CARD} />
        <text x="148" y="222" fontFamily="monospace" fontSize="20" fontWeight="700" fill={CARD}>
          ?
        </text>
      </>
    );
  if (role === "owner")
    return (
      <>
        {body}
        <path d="M98 168l22 30 22-30" fill={CARD} />
        <path d="M86 172l-6 68M154 172l6 68" stroke={INK} strokeOpacity="0.33" strokeWidth="5" />
        <rect x="140" y="204" width="22" height="16" fill="none" stroke={SIGNAL} strokeWidth="3" />
      </>
    );
  if (role === "inside")
    return (
      <>
        {body}
        <path d="M100 168c6 12 14 18 20 18s14-6 20-18" fill={CARD} />
        <path d="M80 112c0-30 18-50 40-50s40 20 40 50" fill="none" stroke={INK} strokeWidth="6" />
        <rect x="72" y="104" width="14" height="24" fill={INK} />
        <rect x="154" y="104" width="14" height="24" fill={INK} />
        <path d="M78 128c0 18 14 28 30 28" fill="none" stroke={INK} strokeWidth="3" />
      </>
    );
  if (role === "export")
    return (
      <>
        {body}
        <path d="M100 168c6 12 14 18 20 18s14-6 20-18" fill={CARD} />
        <path d="M104 170l16 44 16-44" fill="none" stroke={SIGNAL} strokeWidth="5" />
        <rect x="110" y="212" width="20" height="16" fill={CARD} stroke={INK} strokeWidth="2" />
      </>
    );
  return (
    <>
      {body}
      <path d="M96 168l24 50 24-50z" fill={CARD} />
      <path d="M96 168l24 50M144 168l-24 50" stroke="#000" strokeOpacity="0.2" strokeWidth="4" />
      {gender === "M" ? <path d="M114 176h12l4 10-6 40h-8l-6-40z" fill={SIGNAL} /> : <circle cx="120" cy="186" r="5" fill={SIGNAL} />}
    </>
  );
}

export function PersonaAvatar({
  look,
  size = 120,
  speaking = false,
  dimmed = false,
  badge,
  title,
}: {
  look: PersonaLook;
  size?: number;
  speaking?: boolean;
  dimmed?: boolean;
  badge?: ReactNode;
  title?: string;
}) {
  const skin = look.skin ?? SKIN[0];
  const hairColor = look.hair ?? HAIR[0];
  return (
    <div className="relative inline-block" style={{ width: size, height: size }}>
      <svg
        viewBox="0 0 240 240"
        width={size}
        height={size}
        role="img"
        aria-label={title ?? look.key}
        className={`block border-2 border-border bg-card transition-[filter,opacity] duration-300 ${dimmed ? "opacity-55 grayscale" : ""}`}
      >
        <defs>
          <pattern id={`grid-${look.key}`} width="16" height="16" patternUnits="userSpaceOnUse">
            <path d="M16 0H0V16" fill="none" stroke={INK} strokeOpacity="0.06" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="240" height="240" fill={`url(#grid-${look.key})`} />
        {look.role === "buyer" ? <rect x="16" y="16" width="16" height="16" fill={SIGNAL} /> : <rect x="208" y="16" width="16" height="16" fill={SIGNAL} />}
        {look.country ? (
          <g>
            <rect x="16" y="16" width="40" height="22" fill={INK} />
            <text x="36" y="32" textAnchor="middle" fontFamily="monospace" fontSize="12" fontWeight="700" fill={CARD}>
              {look.country}
            </text>
          </g>
        ) : null}
        {look.hairStyle === "long" || look.hairStyle === "bob" ? <Hair style={look.hairStyle} color={hairColor} /> : null}
        <rect x="108" y="130" width="24" height="40" fill={skin} />
        <ellipse cx="120" cy="104" rx="34" ry="40" fill={skin} />
        {look.hairStyle && look.hairStyle !== "long" && look.hairStyle !== "bob" ? <Hair style={look.hairStyle} color={hairColor} /> : null}
        <BeardShape beard={look.beard ?? "none"} color={hairColor} />
        {look.role === "neutral" ? null : <Mouth speaking={speaking} />}
        <Attire role={look.role} cloth={look.cloth ?? CLOTH.account[0]} gender={look.gender} />
        {speaking ? (
          <g className="persona-voice">
            {[0, 1, 2, 3].map((i) => (
              <rect key={i} x={186 + i * 9} y="196" width="5" height="28" fill={SIGNAL} style={{ animationDelay: `${i * 0.12}s` }} />
            ))}
          </g>
        ) : null}
      </svg>
      {speaking ? <span className="pointer-events-none absolute -inset-1 border-2 border-primary persona-ring" /> : null}
      {badge ? <div className="absolute -bottom-2 left-1/2 -translate-x-1/2">{badge}</div> : null}
    </div>
  );
}
