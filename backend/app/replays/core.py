"""Arena replay -> condensed spoken dialogue (ElevenLabs text-to-dialogue). Standard library only,
shared by the API (app.replays.service) and the CLI (audio/reenact.py)."""

import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARENA = "https://arena.tacto.ai"
XI = "https://api.elevenlabs.io"
MODEL = "eleven_v3"
SEED = 4242
CHUNK_CHARS = 1900
PIECE_CHARS = 900
CHUNK_VOICES = 9
MP3_BITRATE = 128_000
SID = re.compile(r"^[0-9a-f]{16}$")


class ArenaError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"arena {status}: {detail}")
        self.status = status


def load_json(name: str, default=None):
    for p in (HERE / "data" / name, HERE.parents[2] / "audio" / name):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return default


def load_assets() -> dict:
    return {
        "voices": load_json("voices.json", {}),
        "catalog": load_json("catalog.json", {"cases": {}, "sessions": {}}),
        "reasons": load_json("reasons.json", {}),
    }


def arena_get(path: str, key: str, base: str = ARENA):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"User-Agent": "clooless-replays", "X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise ArenaError(e.code, e.read().decode("utf-8", errors="replace")[:200]) from None


def list_sessions(key: str, sets=("practice", "sandbox", "final"), base: str = ARENA) -> list[dict]:
    out = []
    for s in sets:
        try:
            rows = arena_get(f"/v1/sessions?set={s}", key, base)
        except ArenaError:
            continue
        out += [{**r, "set": s} for r in rows]
    return out


def fetch(sid: str, key: str, base: str = ARENA) -> dict:
    session = arena_get(f"/v1/sessions/{sid}", key, base)
    if session.get("status") != "ended":
        raise ArenaError(409, "session has not ended")
    threads = {sup: arena_get(f"/v1/sessions/{sid}/threads/{sup}", key, base) for sup in session.get("threads", {})}
    return {"session": session, "threads": threads}


# --- text ---------------------------------------------------------------------------------------

FIXES = {"Gr��e": "Grüße", "Gr��en": "Grüßen"}


def repair(text: str, vocab: list[str]) -> str:
    """The arena sends U+FFFD for non-ASCII letters; restore € and known names."""
    text = re.sub("�(?=\\s?[0-9])", "€", text)
    for bad, good in FIXES.items():
        text = text.replace(bad, good)
    if "�" not in text:
        return text

    def fix(m: re.Match) -> str:
        word = m.group(0)
        pat = re.compile("^" + re.escape(word).replace("�", ".") + "$")
        return next((v for v in vocab if pat.match(v)), word.replace("�", ""))

    return re.sub(r"[\w�&.\-]*�[\w�&.\-]*", fix, text)


GREET = re.compile(
    r"^(dear|hello|hallo|hi|good (morning|afternoon|day)|guten tag|sehr geehrte|grüß gott|servus|moin|dzień dobry|"
    r"dobrý den)\b[^,.!]{0,40}[,!]\s*",
    re.IGNORECASE,
)
CLOSE = re.compile(
    r"^(kind regards|best regards|warm regards|regards|best wishes|yours (sincerely|faithfully|truly)|sincerely|"
    r"mit freundlichen|viele grüße|beste grüße|freundliche grüße|liebe grüße|gruß|grüße|lg\b|"
    r"with (kind|best) regards|best,?$|thanks,?$|cheers)",
    re.IGNORECASE,
)
BOILER = re.compile(
    r"(thank you for your (enquiry|inquiry|message|reply|e-?mail|response|patience)|please find (our|the|attached)|"
    r"remains valid for|look forward to (hearing|your)|do not hesitate|^that remains\.?$|please let us know|"
    r"hope (to hear|this helps)|feel free to|at your disposal|we appreciate your (interest|time)|"
    r"we have reviewed your offer and can adjust|our order confirmation is attached|"
    r"(stays|is|remains) valid (for )?\d+ days|our price follows our main cost drivers)",
    re.IGNORECASE,
)
KEY = re.compile(
    r"(\d|€|eur\b|offer|price|accept|final|package|commit|volume|index|payment|ppm|deal|agree|discount|lead time|"
    r"contract|cannot|can't|minimum|walk|alternative|quote|data you cited|holds)",
    re.IGNORECASE,
)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9€\"'(])")


def units(body: str) -> list[str]:
    lines = [l.strip() for l in body.replace("\r", "").split("\n")]
    for i, l in enumerate(lines):
        if CLOSE.match(l):
            lines = lines[:i]
            break
    lines = [GREET.sub("", l, count=1).strip() for l in lines if l]
    lines = [l[0].upper() + l[1:] for l in lines if l]
    out = []
    for l in lines:
        if re.match(r"^\d+[.)]\s", l) or l.startswith(("- ", "• ")):
            out.append(re.sub(r"\s*\([^)]*\)", "", l))
        else:
            out += [GREET.sub("", x.strip(), count=1) for x in SENT.split(l) if x.strip()]
    out = [u[0].upper() + u[1:] for u in out if u]
    return out


REFS = re.compile(r"\s*\((?:[a-z]{2,6}:[^()]*)\)")


def speakable(text: str) -> str:
    return re.sub(r"\s{2,}", " ", REFS.sub("", text)).replace(" .", ".").strip()


def condense(body: str, limit: int | None) -> str:
    us = [u for u in units(body) if not BOILER.search(u)]
    if not us:
        return ""
    if not limit:
        return " ".join(us)
    prio = [
        (4 if re.search(r"\d", u) else 0) + (2 if KEY.search(u) else 0) + (1 if i == 0 else 0) for i, u in enumerate(us)
    ]
    order = sorted(range(len(us)), key=lambda i: (-prio[i], i))
    keep, total = set(), 0
    for i in order:
        if total + len(us[i]) + 1 <= limit:
            keep.add(i)
            total += len(us[i]) + 1
    if not keep:
        best = us[order[0]]
        cut = best[:limit].rsplit(",", 1)[0] if "," in best[:limit] else best[:limit].rsplit(" ", 1)[0]
        return cut.rstrip(",;: ") + " …"
    return " ".join(us[i] for i in sorted(keep))


# --- casting ------------------------------------------------------------------------------------

FEMALE = {
    "anna",
    "andrea",
    "birgit",
    "claudia",
    "eva",
    "jana",
    "joanna",
    "julia",
    "katharina",
    "katarzyna",
    "monika",
    "petra",
    "sabine",
    "susanne",
    "agnieszka",
    "magdalena",
    "zuzana",
    "lenka",
    "tereza",
    "marta",
    "maria",
    "kinga",
    "eszter",
    "zsófia",
    "ayşe",
    "elif",
    "zeynep",
    "sandra",
    "nicole",
    "stefanie",
    "christine",
    "barbara",
    "ewa",
    "veronika",
    "martina",
    "lucie",
    "ilona",
    "judit",
    "gabriele",
    "ursula",
    "heike",
    "anja",
    "nina",
    "doris",
    "elke",
    "karin",
    "renate",
    "sophie",
    "laura",
    "lisa",
    "lena",
    "hanna",
    "ingrid",
    "irene",
}
MALE_A = {"luca", "andrea_it", "nikola", "kuba", "jarda", "honza", "sasha", "mustafa", "attila"}


def name_gender(full: str | None) -> str | None:
    if not full:
        return None
    first = full.split()[0].lower()
    if first in FEMALE or full.split()[-1].lower().endswith(("ová", "ova", "ska", "cka", "dzka")):
        return "F"
    if first in MALE_A:
        return "M"
    if first.endswith("a") and len(first) > 2:
        return "F"
    return "M"


def contact_of(thread: dict, vocab: list[str]) -> tuple[str | None, str | None]:
    for m in thread.get("messages", []):
        if m.get("from") == "buyer":
            continue
        lines = [l.strip() for l in repair(m.get("body") or "", vocab).splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if CLOSE.match(l) and i + 1 < len(lines):
                name = lines[i + 1]
                role = lines[i + 2].split(",")[0].strip() if i + 2 < len(lines) else None
                if 1 < len(name.split()) <= 4 and not re.search(r"\d|GmbH|s\.r\.o|Sp\. z|AG\b|KG\b", name):
                    return name, role
        if m.get("from_name"):
            return None, m["from_name"].split(",")[0].strip()
    return None, None


def stable(key: str, n: int) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % n


def cast(suppliers: list[str], contacts: dict, voices: dict) -> dict:
    """One voice per supplier, all different within the session and from buyer and narrator."""
    pool = voices.get("pool", {})
    used = {voices["buyer"]["id"], voices["narrator"]["id"]}
    out = {}
    for sup in suppliers:
        entry = dict(voices.get("suppliers", {}).get(sup) or {})
        name, role = contacts.get(sup, (None, None))
        gender = name_gender(name) or entry.get("gender") or (entry.get("voice") or {}).get("gender") or "MF"[stable(sup, 2)]
        tone = entry.get("tone_group") or "sales"
        voice = entry.get("voice")
        if not voice or voice.get("gender") != gender or voice["id"] in used:
            mine = pool.get(gender, {})
            prefs = mine.get(tone, []) + [v for t, vs in mine.items() if t != tone for v in vs]
            prefs += [v for g in pool.values() for vs in g.values() for v in vs]
            prefs = [v for v in prefs if v["id"] not in used]
            voice = prefs[0]
        used.add(voice["id"])
        out[sup] = {
            **entry,
            "contact": name or entry.get("contact"),
            "role": role or entry.get("role"),
            "gender": gender,
            "voice": {"id": voice["id"], "name": voice["name"]},
        }
    return out


# --- script -------------------------------------------------------------------------------------


def sheet_terms(sheet: dict) -> dict:
    return {k: v for k, v in (sheet.get("terms") or {}).items() if v is not None}


def money(v) -> str:
    return f"€{float(v):,.2f}"


def build(data: dict, assets: dict, session_chars: int = 0) -> dict:
    """session_chars 0 voices every turn in full; a positive cap condenses and drops low-priority turns."""
    session, threads = data["session"], data["threads"]
    sid = session["session_id"]
    voices = assets["voices"]
    catalog = assets.get("catalog", {})
    case = catalog.get("cases", {}).get(session.get("case_id"), {})
    vocab = sorted(
        {w for s in voices.get("suppliers", {}).values() for w in re.findall(r"[\w&.\-]+", s.get("company", ""))}
    )
    vocab += ["Grüße", "Grüßen", "für", "Angebot", "Größe", "Qualität", "Lieferung", "Zahlungsziel"]
    sups = list(threads)
    contacts = {s: contact_of(t, vocab) for s, t in threads.items()}
    casting = cast(sups, contacts, voices)
    reasons = assets.get("reasons", {}).get(sid, {})

    turns = []
    rows: list[tuple[int, int, int, dict]] = []
    for si, sup in enumerate(sups):
        sheets_by_id = {}
        rnd = 0
        sends = list((reasons.get("sends") or {}).get(sup, []))
        for k, m in enumerate(threads[sup].get("messages", [])):
            buyer = m.get("from") == "buyer"
            if buyer:
                rnd += 1
            for sh in m.get("term_sheets") or []:
                if sh.get("term_sheet_id"):
                    sheets_by_id[sh["term_sheet_id"]] = sheet_terms(sh)
            action = (m.get("action") or {}).get("type")
            body = repair(m.get("body") or "", vocab)
            if action == "accept":
                tid = m["action"].get("term_sheet_id")
                terms = [{"id": tid, "label": "accepted", "terms": sheets_by_id.get(tid, {})}]
                close = reasons.get("close") or {}
                reason = close.get("reason") if close.get("supplier") in (None, sup) else None
            else:
                terms = [
                    {
                        "id": sh.get("term_sheet_id"),
                        "label": ("package" if buyer else ("standing offer" if j == 0 else "alternative")),
                        "terms": sheet_terms(sh),
                    }
                    for j, sh in enumerate(m.get("term_sheets") or [])
                ]
                reason = sends.pop(0) if buyer and sends else None
            text = condense(body, (230 if buyer else 280) if session_chars else None) if body else ""
            placeholder = not text and bool(action)
            if placeholder:
                text = {"accept": "We accept your offer.", "walk_away": "We will not continue."}.get(action, "")
            turns.append(
                {
                    "speaker": "buyer" if buyer else sup,
                    "supplier": sup,
                    "kind": "turn",
                    "round": rnd,
                    "subject": repair(m.get("subject") or "", vocab),
                    "text": text,
                    "full": body,
                    "condensed": bool(body) and text != body.strip(),
                    "placeholder": placeholder,
                    "status": m.get("status"),
                    "action": action,
                    "terms": terms,
                    "reason": reason,
                }
            )
            rows.append((rnd, 0 if not buyer else -1, si, turns[-1]))
    rows.sort(key=lambda r: (r[0], r[2], r[1]))
    ordered = [r[3] for r in rows]

    result = session.get("result") or {}
    names = [casting[s].get("company") or s for s in sups]
    short = [re.sub(r"\s+(GmbH.*|AG|KG|s\.r\.o\.|Sp\. z o\.o\.|a\.s\.|Kft\.|Co\..*)$", "", n) for n in names]
    cid = (session.get("case_id") or "").replace("case:", "")
    title = case.get("title")
    intro = (
        f"Case {cid}{': ' + title if title else ''}. Hollerau Pumpenwerke negotiates with "
        + (", ".join(short[:-1]) + " and " + short[-1] if len(short) > 1 else short[0])
        + ". The buyer is our agent, Par-lee."
    )
    house = case.get("house")
    if result.get("deal"):
        win = result.get("supplier_id")
        price = (result.get("terms") or {}).get("unit_price")
        outro = f"Deal with {short[sups.index(win)] if win in sups else 'the supplier'}"
        outro += f" at {money(price)} per unit" if price is not None else ""
        outro += f" after {session.get('rounds')} rounds. Score {result.get('score', 0):.2f}"
    else:
        outro = f"No deal ({(session.get('end_reason') or '').replace('_', ' ')}). Score {result.get('score', 0):.2f}"
    outro += f"; Tacto's house agent scored {house:.2f} on this case." if house is not None else "."

    parts = [{"speaker": "narrator", "kind": "narration", "say": intro, "text": intro.replace("Par-lee", "Parley")}]
    parts += ordered
    parts.append({"speaker": "narrator", "kind": "narration", "say": outro, "text": outro})

    budget = session_chars - len(intro) - len(outro)
    prio = []
    last = {}
    for i, t in enumerate(parts):
        if t["kind"] == "turn":
            last[(t["speaker"], t["supplier"])] = i
    for i, t in enumerate(parts):
        if t["kind"] == "narration":
            continue
        p = 3
        if t["round"] == 0 or t["action"] == "accept" or t["status"] in ("agreed", "final_offer"):
            p = 0
        elif last.get((t["speaker"], t["supplier"])) == i or t["speaker"] == "buyer" and t["round"] == 1:
            p = 1
        elif t["round"] <= 2:
            p = 2
        prio.append((p, i))
    spoken = set()
    for p, i in sorted(prio):
        c = len(speakable(parts[i]["text"])) + 30
        if parts[i]["text"] and (not session_chars or c <= budget):
            spoken.add(i)
            budget -= c
    first_seen = set()
    for i, t in enumerate(parts):
        if t["kind"] == "narration":
            t["spoken"] = True
            continue
        t["spoken"] = i in spoken
        if not t["spoken"]:
            continue
        if t["speaker"] == "buyer":
            t["say"] = speakable(t["text"])
            continue
        c = casting[t["speaker"]]
        tags = [c.get("accent") or ""]
        if t["speaker"] not in first_seen:
            tags.append(c.get("style") or "")
            first_seen.add(t["speaker"])
        elif t["status"] == "final_offer":
            tags.append("firmly")
        elif t["status"] == "agreed":
            tags.append("pleased")
        t["say"] = "".join(f"[{x}] " for x in tags if x) + speakable(t["text"])

    speakers = {
        "narrator": {"label": "Narrator", "kind": "narrator", "voice": voices["narrator"]["name"]},
        "buyer": {"label": "Parley · Hollerau Pumpenwerke", "kind": "buyer", "voice": voices["buyer"]["name"]},
    }
    for s in sups:
        c = casting[s]
        speakers[s] = {
            "label": " · ".join(x for x in (c.get("contact"), c.get("role")) if x) or (c.get("company") or s),
            "kind": "supplier",
            "id": s,
            "company": c.get("company"),
            "contact": c.get("contact"),
            "role": c.get("role"),
            "country": c.get("country"),
            "voice": c["voice"]["name"],
            "voice_id": c["voice"]["id"],
            "accent": c.get("accent"),
            "style": c.get("style"),
        }
    voice_ids = {"narrator": voices["narrator"]["id"], "buyer": voices["buyer"]["id"]} | {
        s: casting[s]["voice"]["id"] for s in sups
    }

    return {
        "session_id": sid,
        "case_id": session.get("case_id"),
        "set": session.get("set"),
        "title": title,
        "type": case.get("type"),
        "rounds": session.get("rounds"),
        "end_reason": session.get("end_reason"),
        "house_score": house,
        "agent": catalog.get("sessions", {}).get(sid, {}).get("agent"),
        "replay_url": f"{ARENA}/replay/{sid}",
        "result": {
            k: result.get(k) for k in ("deal", "supplier_id", "score", "c_open", "c_deal", "c_best", "c_alt", "terms")
        },
        "speakers": speakers,
        "model_id": MODEL,
        "turns": parts,
        "_voice_ids": voice_ids,
    }


def inputs_of(script: dict) -> list[tuple[int, dict]]:
    ids = script["_voice_ids"]
    return [
        (i, {"text": t["say"], "voice_id": ids[t["speaker"]]})
        for i, t in enumerate(script["turns"])
        if t.get("spoken") and t.get("say")
    ]


def characters(script: dict) -> int:
    return sum(len(x["text"]) for _, x in inputs_of(script))


def _dialogue(inputs: list[dict], key: str) -> dict:
    body = {"inputs": inputs, "model_id": MODEL, "settings": {"stability": 0.5}, "seed": SEED}
    req = urllib.request.Request(
        XI + "/v1/text-to-dialogue/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps(body).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs {e.code}: {e.read().decode()[:300]}") from None


def pieces(text: str, n: int = PIECE_CHARS) -> list[str]:
    """Split one long turn at sentence (or word) boundaries so every request stays small."""
    if len(text) <= n:
        return [text]
    out, cur = [], ""
    for sent in SENT.split(text):
        while len(sent) > n:
            cut = sent[:n].rsplit(" ", 1)[0] or sent[:n]
            if cur:
                out.append(cur)
                cur = ""
            out.append(cut)
            sent = sent[len(cut) :].lstrip()
        if cur and len(cur) + 1 + len(sent) > n:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def chunks_of(script: dict) -> list[list[tuple[int, dict]]]:
    items = [(i, {**x, "text": p}) for i, x in inputs_of(script) for p in pieces(x["text"])]
    chunks, cur, chars, vs = [], [], 0, set()
    for it in items:
        n = len(it[1]["text"])
        if cur and (chars + n > CHUNK_CHARS or len(vs | {it[1]["voice_id"]}) > CHUNK_VOICES):
            chunks.append(cur)
            cur, chars, vs = [], 0, set()
        cur.append(it)
        chars += n
        vs.add(it[1]["voice_id"])
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(script: dict, key: str) -> tuple[bytes, dict]:
    audio, offset = b"", 0.0
    for chunk in chunks_of(script):
        res = _dialogue([x for _, x in chunk], key)
        mp3 = base64.b64decode(res["audio_base64"])
        spans: dict[int, list[float]] = {}
        for seg in res.get("voice_segments", []):
            j = seg["dialogue_input_index"]
            s, e = seg["start_time_seconds"], seg["end_time_seconds"]
            spans[j] = [min(spans.get(j, [s, e])[0], s), max(spans.get(j, [s, e])[1], e)]
        for j, (ti, _) in enumerate(chunk):
            if j in spans:
                t = script["turns"][ti]
                t.setdefault("start", round(offset + spans[j][0], 3))
                t["end"] = round(offset + spans[j][1], 3)
        audio += mp3
        offset += len(mp3) * 8 / MP3_BITRATE
    return audio, finish(script, audio_name="audio.mp3", duration=offset)


def finish(script: dict, audio_name: str | None, duration: float | None = None) -> dict:
    out = {k: v for k, v in script.items() if not k.startswith("_")}
    out["characters"] = characters(script)
    out["audio"] = audio_name
    out["duration"] = round(duration, 2) if duration else None
    out["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    for t in out["turns"]:
        t.setdefault("start", None)
        t.setdefault("end", None)
        t.pop("say", None)
    return out


def index_entry(r: dict) -> dict:
    res = r.get("result") or {}
    return {
        "session_id": r["session_id"],
        "case_id": r["case_id"],
        "set": r.get("set"),
        "title": r.get("title"),
        "type": r.get("type"),
        "rounds": r.get("rounds"),
        "end_reason": r.get("end_reason"),
        "deal": res.get("deal"),
        "score": res.get("score"),
        "house_score": r.get("house_score"),
        "agent": r.get("agent"),
        "suppliers": [s.get("company") for s in r["speakers"].values() if s["kind"] == "supplier"],
        "voices": {k: s["voice"] for k, s in r["speakers"].items()},
        "characters": r.get("characters"),
        "duration": r.get("duration"),
    }


def summary(script: dict) -> str:
    lines = []
    for t in script["turns"]:
        mark = " " if t.get("spoken") else "-"
        lines.append(f"{mark} {t['speaker']:>10}: {t.get('say') or t['text']}")
    return "\n".join(lines)
