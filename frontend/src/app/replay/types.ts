export type Terms = Record<string, unknown>;
export type Sheet = { id?: string | null; label: string; terms: Terms };

export type Turn = {
  speaker: string;
  supplier?: string;
  kind: "turn" | "narration";
  round?: number;
  subject?: string;
  text: string;
  full?: string;
  condensed?: boolean;
  placeholder?: boolean;
  spoken?: boolean;
  status?: string | null;
  action?: string | null;
  terms?: Sheet[];
  reason?: string | null;
  start: number | null;
  end: number | null;
};

export type Speaker = {
  label: string;
  kind: "narrator" | "buyer" | "supplier";
  id?: string | null;
  company?: string | null;
  contact?: string | null;
  role?: string | null;
  country?: string | null;
  voice: string;
  accent?: string | null;
  style?: string | null;
};

export type Replay = {
  session_id: string;
  case_id: string;
  set: string;
  title?: string | null;
  type?: string | null;
  rounds?: number | null;
  end_reason?: string | null;
  house_score?: number | null;
  agent?: string | null;
  replay_url?: string | null;
  result: {
    deal?: boolean | null;
    supplier_id?: string | null;
    score?: number | null;
    c_open?: number | null;
    c_deal?: number | null;
    c_best?: number | null;
    terms?: Terms | null;
  };
  speakers: Record<string, Speaker>;
  model_id?: string;
  characters?: number;
  duration?: number | null;
  audio: string;
  turns: Turn[];
};

export type Entry = {
  session_id: string;
  case_id: string;
  set: string;
  title?: string | null;
  type?: string | null;
  suppliers?: (string | null)[];
  rounds?: number | null;
  end_reason?: string | null;
  deal?: boolean | null;
  score?: number | null;
  house_score?: number | null;
  agent?: string | null;
  runs?: number | null;
  duration?: number | null;
  status?: "ready" | "generating" | "failed" | "none" | string;
};

export type Generation = { enabled: boolean; public?: boolean; left_today: number | null; session_chars: number | null };

export type Collection = { label: string; agent?: string | null; mean?: number | null; sessions: string[] };
export type Collections = Record<string, Collection>;
