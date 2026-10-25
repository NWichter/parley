# LLM-voiced suppliers: first results (24 Oct 2026)

Parley against the persona bank with `--supplier-llm`: an LLM reads Parley's e-mail and writes the
supplier's reply, while prices, floors and accept / final / walk decisions stay in the persona's
code (see README, "LLM-voiced suppliers"). The reader's bonuses are bounded by design, so compare
agents or versions on the same bank, not absolute levels across banks.

Parley's code changed during the runs (v1.3 to v1.6). Only same-version games are compared below;
the last runs used a pinned worktree of Parley v1.6 (0aa63cc).

## Scores

Parley v1.3 in LLM mode, the same 62 games (9 personas x 8 cases; `announced_steps` missing):

| persona | deterministic | Sonnet 5.5 | Gemini 4 Argon | GPT-6.1 Sol |
|---|---:|---:|---:|---:|
| mirror | 0.683 | 0.691 | 0.708 | 0.710 |
| holder | 0.442 | 0.473 | 0.471 | 0.475 |
| floor_stop | 0.820 | 0.822 | 0.822 | 0.823 |
| short_fuse | 0.856 | 0.858 | 0.856 | 0.857 |
| auction | 0.552 | 0.570 | 0.583 | 0.584 |
| index_lover | 0.738 | 0.747 | 0.748 | 0.749 |
| cash_constrained | 0.804 | 0.827 | 0.845 | 0.833 |
| dup_punisher | 0.461 | 0.489 | 0.499 | 0.503 |
| **all** | **0.676** | **0.691** | **0.698** | **0.698** |

- Over all 180 games (9 x 20, v1.3): deterministic 0.659, Sonnet bank 0.674. Opus 5.5: 0.688 against
  Sonnet's 0.685 on its 89 v1.3 games. DeepSeek v4.1 Flash, GPT-6 Luna (72 games, mixed versions) and
  Grok 4.7, Qwen 3.8 Max (3 games each) behaved the same way.
- **Parley holds up across supplier model families.** The ranking and the deal count (54/62) stay
  the same; the spread comes from how generous each reader is. Gemini, GPT-6.1 Sol and Opus count a
  payment promise in 85-99 % of mails, Sonnet in 17 %.
- **The LLM writer adds nothing measurable**, even against suppliers that read the prose. Pinned v1.6,
  9 x 20: deterministic bank 0.635 with and without the LLM; Haiku bank 0.636 (LLM) vs 0.634 (no LLM).
  Sonnet bank, v1.3: 0.674 vs 0.671.
- **Regression v1.3 to v1.6** on the deterministic bank (no-LLM leg, deterministic): 0.659 to 0.635.
  `announced_steps` 0.806 to 0.708, `auction` 0.498 to 0.449; v1.6 walks away in case E02 at a score
  of 0 (`holder`, `announced_steps`).

## What the supplier readers reacted to

- "What is the best price you could sign today on one of these packages?" is the line suppliers
  react to most. Sonnet's supplier answered "You ask for the best price we could sign today. This is
  it:". Every non-Claude reader reads it as a **deadline** (GPT-6.1 Sol 35x, DeepSeek 28x, Luna 17x,
  Gemini 15x in 72 games; Sonnet about 1 %), and DeepSeek also as a walk threat and as condescending.
- "each works equally well for us, so please choose the one that suits you best" reads as
  take-it-or-leave-it for DeepSeek (a walk threat, 36x) and as a dismissive lowball for Haiku.
- Rival quotes ("We hold another quote for this part at EUR 82.94 per unit") count as "mentions
  alternatives" for every reader. Together with a deep RFQ anchor (1.2-1.8 rooms below floor), an
  impatient owner walks: "Both of your packages are far below any basis for us."
- Readers find about one figure without a data ID per mail, mostly the binding volume restated
  without its `fc:` ID after round 1; `dup_punisher` then asks for the data reference.

## Parley's mails

- **Invented supplier statements (LLM mode only).** The opening paragraph credits the supplier with
  things its last e-mail does not say: "your earlier offer to go through your calculation together",
  "the goodwill you showed us in the past", "you call us an important reference customer". v1.6:
  goodwill or history in 60 of 793 mails (46 with no basis in the supplier's mail), "go through the
  calculation" in 72 (28 with no basis). The source is the supplier profile from
  `agent/cache/personas.json` (history from meeting notes), which the writer gets as context and then
  presents as the supplier's current message.
- **Empty promises (LLM mode only):** "We are happy to explain where our index figures come from"
  (24 mails in v1.6), never followed by an explanation.
- **Greeting:** no broken salutation in any LLM-voiced run. Every mail opens "Dear <First Last>," or
  "Dear Sir or Madam,".

## Lessons for Parley's writer and analyst

1. Writer: give the model only the supplier's last e-mail as "what they said"; profile notes only as
   tone hints. Reject an opening that names an offer, a promise, goodwill or the past unless the last
   supplier e-mail contains it, and reject "we will explain / we are happy to share".
2. Writer: drop "today" from the closing question ("What is the best price you could offer on one of
   these packages?"), and soften "each works equally well for us, so please choose" ("Either package
   works for us; which one fits you better?"). Non-Claude readers hear pressure in both.
3. Analyst: in RFQs with an impatient owner, do not combine an anchor far below floor with a rival
   quote in round 1.
4. Writer: repeat the `fc:` ID every time the binding volume is restated.
5. The LLM opening costs tokens (165 vs 125 words per mail) without moving the score; keep it short
   or switch it off for impatient suppliers.
6. Check v1.6 against v1.3 on `announced_steps`, `auction` and the E02 walk-away before the final.

## Cost

Supplier model cost per game: Haiku, Luna, DeepSeek about $0.003; Gemini and GPT-6.1 Sol $0.04;
Sonnet $0.06; Grok $0.11; Opus $0.12; Fable $0.20. Per-run reports (local, gitignored): `eval/results/<ts>/report.md`, with `compare.md` and
`audit_*.md` next to them in `20261024-175111` (Sonnet) and `20261024-185514` (Haiku, pinned v1.6);
cross-model matrices and the per-game version map in `eval/results/20261024-llm-bank/`.
