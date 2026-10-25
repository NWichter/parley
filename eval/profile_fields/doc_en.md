# Structured supplier profile fields

Status {created}. File: `agent/cache/profile_fields.json` ({n} suppliers, `complete: {complete}`). Scripts: `eval/profile_fields/`.
{status}

## Bottom line

- Every detailed supplier (46) has 17 structured fields. Each non-null value carries evidence: source ID + verbatim quote, **checked by code** against the source text. A quote that is not in the named source drops the value. Null means "no evidence in the package", never "false".
- The sources are **only Tacto's data package**, which a real buyer also has: vendor master, `supplier_intel`, contract clauses, documents + `correspondence/`, news, quotes, the notes in `negotiation_history`, sourcing and cost drivers. No replays, no practice or sandbox runs, no experiments, no outcome data. The fields do not know how a supplier behaved in any negotiation that we or others played.
- The schema and the extraction prompt were frozen before any result was read (`eval/profile_fields/prompt_v1.txt`, sha256 `{sha}`). No field is tuned per supplier.
- **The values are data, not rules.** The policy maps each field the same way for every supplier (section 5).
- Release marking: every evidence item has `release` (today `package-v1` everywhere). After the Sunday 08:00 release, add the release sources to `bundle.py` with `release: "sunday"` and run everything again (section 6).

## 1. Schema (v1)

Per supplier there are two parts. `summary` holds the flat values for the policy. `fields` holds the value, the evidence `[{source, quote, kind, date, release}]`, the confidence (`high|medium|low`) and the agreement. List fields have one entry per item, and each item has its own evidence.

| Field | Value | Definition (short; full text in `prompt_v1.txt`) |
|---|---|---|
| `approval_threshold_pct` | number / null | price change in % above which the negotiator needs internal approval |
| `approval_cycle` | text / null | who approves and how often, as stated |
| `approval_cycle_days` | int / null | cadence in days (every two weeks → 14) |
| `preferred_levers` | list of levers | non-price term that the supplier asks for, offers or is open to (`stance`, `condition`) |
| `avoid_levers` | list of levers | term that the supplier explicitly rejects. Not inferred as the opposite of a preferred lever |
| `capacity_tight` | true / false / null | true = a stated constraint; false only with an explicit statement of free capacity |
| `price_freeze` | {until, scope} / null | blanket restriction on individual price reductions. The contract's "fixed for each calendar year" does not count |
| `cash_need` | weak / strong / null | strong = financial pressure; weak = only a request for faster payment or a cash discount, or a credit index that got worse |
| `hedged_inputs` | list {input, until, series} | inputs bought forward or covered at fixed prices. The models name the input. Code maps it to index series (keyword table ∩ cost drivers of the supplier's parts) |
| `uninformed_owner` | true / null | the negotiator does not track own costs or market prices |
| `decision_maker` | owner / kam / sales_director / inside_sales / export / null | who negotiates, from notes and letters only (not from `owner_type`) |
| `patience` | low / med / high / null | only from an explicit statement about rounds, deadlines or ultimatums |
| `past_goodwill_claim` | true / null | the supplier refers to a past sacrifice and expects something in return |
| `volume_posture` | accepts_below_cost / wants_volume / null | accepted prices below full cost to keep the volume, or focuses on volume / binding commitments |
| `open_calculation` | true / null | offered to walk us through its calculation |
| `margin_floor` | true / null | "cannot sign anything that lowers our margin" |
| `account_deprioritised` | true / null | we are no longer strategic, or replies have become slower |

Lever enum (always the buyer's move): `payment_days_short`, `payment_days_long`, `lead_time_slack` (incl. call-off flexibility), `contract_months_longer`, `volume_commitment`, `index_clause`, `effective_date_later`, `ppm_relaxed`, `chargeback_none`, `tooling`.

What changed from the draft after the reviews (Gemini 4 Argon and Codex; texts in `eval/profile_fields/reviews/`):
- null instead of false or "none" when there is no evidence;
- evidence per list item;
- `approval_cycle_days` as an integer;
- hedges with `until`; code maps them to series, so the models do not guess IDs;
- no inference of the opposite;
- `decision_maker` only from correspondence, with `sales_director` added;
- `patience` only from explicit statements. It is null for all 46, because the package has no such statement;
- contract boilerplate is not a signal;
- new fields `past_goodwill_claim`, `volume_posture`, `open_calculation`, `margin_floor` and `account_deprioritised` (the cues from `data-package.en.md` 1.3).

Not adopted: Codex's full split of approval rules and lever stance into separate objects (too fine for the policy). Also not adopted: its proposal to drop the "below full cost" notes as outcome data. These notes are Tacto's own purchasing records, which a real buyer has.

## 2. Method

1. `bundle.py` builds one source bundle per supplier from the package: 830 units, at most 7.7 kB per supplier. Each unit has an ID, and the evidence must name it.
2. Extraction: Claude Opus 5.5 (effort high, `ANTHROPIC_API_KEY_2`) with `prompt_v1.txt`, all 46 suppliers.
3. Independent cross-check:
   - Gemini 4 Argon (`agy --model gemini-4-argon-high`, sources inline), all 46 suppliers. For 70530 that call hung three times; it ran with `--model gemini-4-argon --effort high`.
   - Codex (`codex exec`) on a fixed sample of 10, chosen before any results: 70110, 70190, 70230, 70280, 70350, 70420, 70520, 70550, 70650, 70730. The sample covers all categories and 3 never-played suppliers.
4. `clean.py` checks every quote of every model against its source. Whitespace, quote marks and `*` are normalised, and `...` is allowed between verbatim parts. A value without a verified quote is dropped.
5. `merge.py` compares the canonical values of the models per field. Where they differ, Claude Opus 5.5 decides from the sources and the same definitions. It does not see which model proposed which value. Values decided this way get confidence `low`.
   Caveat: the adjudicator is the same model as the extractor. Of 19 disputes it sided with the Claude value in 15. The disputed values are therefore marked `low`, and the policy should not build hard caps on them.
6. `final.py` writes `profile_fields.json`, `stats.py` the agreement and `docs.py` this document.

Quote check: {quotes}.
Cost: {cost}.

## 3. Agreement per field

"Claude = Gemini" means the canonical values are equal (both null counts as equal). The second column counts only the cases in which at least one model found something. It is the stricter number.

{stats}

Item level (list fields, Claude vs Gemini, |∩| / |∪|): {items}.

How often each field is set in the final file:

{counts}

## 4. The 9 never-played suppliers

There is no practice run for these suppliers (list from `supplier-atlas.en.md`; used here only to highlight them, not for the extraction). For them, these fields are the only structured knowledge.

{never}

All 46:

{all}

## 5. How the policy should use each field (same mapping for every supplier)

- `approval_threshold_pct` (2 % wherever it is set): the negotiator cannot go further without the committee.
  - Keep the price step per round ≤ the threshold.
  - A total move above the threshold needs a committee cycle (`approval_cycle_days`, 14).
  - Put value beyond the threshold into terms, not into the price.
- `price_freeze`: do not spend facts on price reductions. Lead with terms (payment, volume, contract length, call-off) and cap the price depth (data-package proposal: about 3 %).
- `preferred_levers`: build offer packages from these first. `condition` says what the supplier wants in return (for example 10-day payment against a cash discount).
- `avoid_levers`: never offer or demand these.
- `capacity_tight`:
  - `true` → trade lead-time slack, call-off flexibility or a volume commitment instead of price. Do not threaten to move volume.
  - `false` → volume and utilisation are levers.
- `cash_need`: `strong` → lead with short payment (`payment_days_short`) in exchange for price; `weak` → offer it as one package option.
- `hedged_inputs[].series`: do not cite a fall of these series before `until`. Do cite the hedge if the supplier argues with a rise of that input.
- `uninformed_owner` → three verified facts per message, in every round.
- `decision_maker`: sets tone and expectations. owner = personal, partnership; kam / sales_director = process, committee; inside_sales = list conditions.
- `past_goodwill_claim` → acknowledge the past gesture explicitly in the first mail.
- `volume_posture`: `accepts_below_cost` → little room on price, prefer volume security over depth; `wants_volume` → a volume commitment is the main currency.
- `open_calculation` → ask for the cost breakdown and argue line by line.
- `margin_floor` → do not push for margin cuts. Move the ask to input-cost pass-through and terms.
- `account_deprioritised` → keep the thread short and make the first offer attractive.
- `confidence = low` (disputed and then decided): use only for soft choices (tone, order of packages), never for hard caps.

## 6. Re-run / continue

```bash
cd eval/profile_fields
python -I bundle.py                                   # source bundles from the package (+ release later)
cd ../../agent && uv run python -I ../eval/profile_fields/run_claude.py   # Opus extraction (cache in out/claude)
cd ../eval/profile_fields
python -I run_cli.py gemini 6                         # Gemini via agy, all 46 (cache in out/gemini)
python -I run_cli.py codex 5 sup:70110 sup:70190 sup:70230 sup:70280 sup:70350 sup:70420 sup:70520 sup:70550 sup:70650 sup:70730
cd ../../agent && uv run python -I ../eval/profile_fields/merge.py adjudicate   # Opus on disagreements (out/adjud)
cd ../eval/profile_fields
python -I final.py ../../agent/cache/profile_fields.json && python -I stats.py && python -I docs.py
```

To extract one supplier again, delete its files in `out/*/`. For the Sunday release: add its CSV rows and letters to `bundle.py` with `release: "sunday"`, delete `out/` and run everything again.
