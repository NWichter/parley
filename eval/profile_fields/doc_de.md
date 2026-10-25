# Strukturierte Lieferantenprofil-Felder

Stand {created}. Datei: `agent/cache/profile_fields.json` ({n} Lieferanten, `complete: {complete}`). Skripte: `eval/profile_fields/`.
{status}

## Ergebnis

- Jeder detaillierte Lieferant (46) hat 17 strukturierte Felder. Jeder Wert ungleich null trägt Belege: Quell-ID + wörtliches Zitat, **per Code** gegen den Quelltext geprüft. Steht das Zitat nicht in der genannten Quelle, fällt der Wert weg. Null heißt „kein Beleg im Paket“, nie „falsch“.
- Quellen sind **nur Tactos Datenpaket**, das auch ein echter Einkäufer hat: Lieferantenstamm, `supplier_intel`, Vertragsklauseln, Dokumente + `correspondence/`, News, Angebote, die Notizen in `negotiation_history`, Sourcing und Kostentreiber. Keine Replays, keine Übungs- oder Sandbox-Läufe, keine Experimente, keine Ergebnisdaten. Die Felder wissen nicht, wie sich ein Lieferant in einer Verhandlung verhalten hat, die wir oder andere gespielt haben.
- Schema und Extraktions-Prompt waren eingefroren, bevor ein Ergebnis gelesen wurde (`eval/profile_fields/prompt_v1.txt`, sha256 `{sha}`). Kein Feld ist je Lieferant angepasst.
- **Die Werte sind Daten, keine Regeln.** Die Policy bildet jedes Feld für alle Lieferanten gleich ab (Abschnitt 5).
- Release-Kennzeichnung: Jeder Beleg hat `release` (heute überall `package-v1`). Nach dem Sonntags-Release um 08:00 die Release-Quellen in `bundle.py` mit `release: "sunday"` aufnehmen und alles neu laufen lassen (Abschnitt 6).

## 1. Schema (v1)

Je Lieferant gibt es zwei Teile. `summary` enthält die flachen Werte für die Policy. `fields` enthält Wert, Belege `[{source, quote, kind, date, release}]`, Konfidenz (`high|medium|low`) und Übereinstimmung. Listenfelder haben einen Eintrag je Element, jedes Element mit eigenen Belegen.

| Feld | Wert | Definition (kurz; voller Text in `prompt_v1.txt`) |
|---|---|---|
| `approval_threshold_pct` | Zahl / null | Preisänderung in %, ab der der Verhandler eine interne Freigabe braucht |
| `approval_cycle` | Text / null | wer freigibt und wie oft, wie genannt |
| `approval_cycle_days` | Ganzzahl / null | Takt in Tagen (alle zwei Wochen → 14) |
| `preferred_levers` | Liste von Hebeln | Nicht-Preis-Bedingung, die der Lieferant verlangt, anbietet oder für die er offen ist (`stance`, `condition`) |
| `avoid_levers` | Liste von Hebeln | Bedingung, die der Lieferant ausdrücklich ablehnt. Nicht als Gegenteil eines bevorzugten Hebels erschlossen |
| `capacity_tight` | true / false / null | true = genannter Engpass; false nur bei ausdrücklich freier Kapazität |
| `price_freeze` | {until, scope} / null | pauschale Sperre individueller Preissenkungen. „Fest je Kalenderjahr“ im Vertrag zählt nicht |
| `cash_need` | weak / strong / null | strong = finanzieller Druck; weak = nur Wunsch nach schnellerer Zahlung oder Skonto, oder ein verschlechterter Bonitätsindex |
| `hedged_inputs` | Liste {input, until, series} | Vorleistungen, die vorab gekauft oder zu Festpreisen gedeckt sind. Die Modelle nennen den Stoff. Code ordnet die Indexreihen zu (Schlagworttabelle ∩ Kostentreiber der Teile des Lieferanten) |
| `uninformed_owner` | true / null | der Verhandler verfolgt eigene Kosten oder Marktpreise nicht |
| `decision_maker` | owner / kam / sales_director / inside_sales / export / null | wer verhandelt, nur aus Notizen und Briefen (nicht aus `owner_type`) |
| `patience` | low / med / high / null | nur aus einer ausdrücklichen Aussage zu Runden, Fristen oder Ultimaten |
| `past_goodwill_claim` | true / null | der Lieferant verweist auf ein früheres Entgegenkommen und erwartet eine Gegenleistung |
| `volume_posture` | accepts_below_cost / wants_volume / null | hat unter Vollkosten abgeschlossen, um die Menge zu halten, oder setzt auf Menge / feste Zusagen |
| `open_calculation` | true / null | hat angeboten, seine Kalkulation mit uns durchzugehen |
| `margin_floor` | true / null | „kann nichts unterschreiben, was unsere Marge senkt“ |
| `account_deprioritised` | true / null | wir sind nicht mehr strategisch, oder die Antworten sind langsamer geworden |

Hebel-Enum (immer der Zug des Einkäufers): `payment_days_short`, `payment_days_long`, `lead_time_slack` (inkl. Abruf-Flexibilität), `contract_months_longer`, `volume_commitment`, `index_clause`, `effective_date_later`, `ppm_relaxed`, `chargeback_none`, `tooling`.

Was sich nach den Reviews am Entwurf geändert hat (Gemini 4 Argon und Codex; Texte in `eval/profile_fields/reviews/`):
- null statt false oder „none“, wenn es keinen Beleg gibt;
- Belege je Listenelement;
- `approval_cycle_days` als Ganzzahl;
- Hedges mit `until`; die Zuordnung zu Reihen macht der Code, die Modelle raten keine IDs;
- kein Erschließen des Gegenteils;
- `decision_maker` nur aus der Korrespondenz, dazu `sales_director`;
- `patience` nur aus ausdrücklichen Aussagen. Es ist für alle 46 null, weil das Paket keine solche Aussage enthält;
- Standardklauseln der Verträge sind kein Signal;
- neue Felder `past_goodwill_claim`, `volume_posture`, `open_calculation`, `margin_floor` und `account_deprioritised` (die Hinweise aus `data-package.md` 1.3).

Nicht übernommen: Codex' vollständige Aufspaltung von Freigaberegeln und Hebel-Haltung in eigene Objekte (zu fein für die Policy). Ebenfalls nicht übernommen: sein Vorschlag, die „unter Vollkosten“-Notizen als Ergebnisdaten zu streichen. Diese Notizen sind Tactos eigene Einkaufsakten, die ein echter Einkäufer hat.

## 2. Methode

1. `bundle.py` baut je Lieferant ein Quellenbündel aus dem Paket: 830 Einheiten, höchstens 7,7 kB je Lieferant. Jede Einheit hat eine ID, und der Beleg muss sie nennen.
2. Extraktion: Claude Opus 5.5 (effort high, `ANTHROPIC_API_KEY_2`) mit `prompt_v1.txt`, alle 46 Lieferanten.
3. Unabhängiger Gegencheck:
   - Gemini 4 Argon (`agy --model gemini-4-argon-high`, Quellen inline), alle 46 Lieferanten. Für 70530 hing dieser Aufruf dreimal; er lief mit `--model gemini-4-argon --effort high`.
   - Codex (`codex exec`) auf einer festen Stichprobe von 10, gewählt vor allen Ergebnissen: 70110, 70190, 70230, 70280, 70350, 70420, 70520, 70550, 70650, 70730. Die Stichprobe deckt alle Kategorien und 3 nie gespielte Lieferanten ab.
4. `clean.py` prüft jedes Zitat jedes Modells gegen seine Quelle. Leerraum, Anführungszeichen und `*` werden normalisiert, `...` ist zwischen wörtlichen Teilen erlaubt. Ein Wert ohne geprüftes Zitat fällt weg.
5. `merge.py` vergleicht je Feld die kanonischen Werte der Modelle. Wo sie abweichen, entscheidet Claude Opus 5.5 anhand der Quellen und derselben Definitionen. Es sieht nicht, welches Modell welchen Wert vorgeschlagen hat. So entschiedene Werte bekommen Konfidenz `low`.
   Vorbehalt: Der Schiedsrichter ist dasselbe Modell wie der Extraktor. Von 19 Streitfällen hat er 15-mal den Claude-Wert bestätigt. Strittige Werte sind deshalb `low` markiert, und die Policy soll darauf keine harten Deckel bauen.
6. `final.py` schreibt `profile_fields.json`, `stats.py` die Übereinstimmung und `docs.py` dieses Dokument.

Zitatprüfung: {quotes}.
Kosten: {cost}.

## 3. Übereinstimmung je Feld

„Claude = Gemini“ heißt: Die kanonischen Werte sind gleich (beide null zählt als gleich). Die zweite Spalte zählt nur die Fälle, in denen mindestens ein Modell etwas gefunden hat. Sie ist die strengere Zahl.

{stats}

Elementebene (Listenfelder, Claude vs. Gemini, |∩| / |∪|): {items}.

Wie oft jedes Feld in der finalen Datei gesetzt ist:

{counts}

## 4. Die 9 nie gespielten Lieferanten

Für diese Lieferanten gibt es keinen Übungslauf (Liste aus `supplier-atlas.md`; hier nur zur Hervorhebung, nicht für die Extraktion). Für sie sind diese Felder das einzige strukturierte Wissen.

{never}

Alle 46:

{all}

## 5. Wie die Policy jedes Feld nutzen soll (gleiche Abbildung für alle Lieferanten)

- `approval_threshold_pct` (überall, wo gesetzt, 2 %): Der Verhandler kann ohne Komitee nicht weiter gehen.
  - Preisschritt je Runde ≤ Schwelle.
  - Eine Gesamtbewegung über der Schwelle braucht einen Komitee-Zyklus (`approval_cycle_days`, 14).
  - Wert jenseits der Schwelle in Bedingungen legen, nicht in den Preis.
- `price_freeze`: keine Fakten für Preissenkungen verbrauchen. Mit Bedingungen führen (Zahlung, Menge, Laufzeit, Abruf) und die Preistiefe deckeln (Vorschlag aus data-package: etwa 3 %).
- `preferred_levers`: Angebotspakete zuerst daraus bauen. `condition` sagt, was der Lieferant dafür will (z. B. Zahlung in 10 Tagen gegen Skonto).
- `avoid_levers`: nie anbieten oder verlangen.
- `capacity_tight`:
  - `true` → Lieferzeit-Spielraum, Abruf-Flexibilität oder Mengenzusage statt Preis. Keine Mengenverlagerung androhen.
  - `false` → Menge und Auslastung sind Hebel.
- `cash_need`: `strong` → mit kurzer Zahlung (`payment_days_short`) gegen Preis führen; `weak` → als eine Paketoption anbieten.
- `hedged_inputs[].series`: einen Rückgang dieser Reihen vor `until` nicht zitieren. Den Hedge zitieren, wenn der Lieferant mit einem Anstieg dieses Stoffs argumentiert.
- `uninformed_owner` → in jeder Runde drei geprüfte Fakten je Nachricht.
- `decision_maker`: bestimmt Ton und Erwartung. owner = persönlich, Partnerschaft; kam / sales_director = Prozess, Komitee; inside_sales = Listenkonditionen.
- `past_goodwill_claim` → das frühere Entgegenkommen in der ersten Mail ausdrücklich anerkennen.
- `volume_posture`: `accepts_below_cost` → wenig Preisspielraum, Mengensicherheit vor Tiefe; `wants_volume` → die Mengenzusage ist die Hauptwährung.
- `open_calculation` → Kostenaufschlüsselung anfordern und Zeile für Zeile argumentieren.
- `margin_floor` → keine Margenkürzung fordern. Auf die Weitergabe von Vorleistungskosten und auf Bedingungen ausweichen.
- `account_deprioritised` → den Thread kurz halten und das erste Angebot attraktiv machen.
- `confidence = low` (strittig und dann entschieden): nur für weiche Entscheidungen (Ton, Reihenfolge der Pakete), nie für harte Deckel.

## 6. Neu laufen lassen / fortsetzen

```bash
cd eval/profile_fields
python -I bundle.py                                   # Quellenbündel aus dem Paket (+ später Release)
cd ../../agent && uv run python -I ../eval/profile_fields/run_claude.py   # Opus-Extraktion (Cache in out/claude)
cd ../eval/profile_fields
python -I run_cli.py gemini 6                         # Gemini über agy, alle 46 (Cache in out/gemini)
python -I run_cli.py codex 5 sup:70110 sup:70190 sup:70230 sup:70280 sup:70350 sup:70420 sup:70520 sup:70550 sup:70650 sup:70730
cd ../../agent && uv run python -I ../eval/profile_fields/merge.py adjudicate   # Opus bei Abweichungen (out/adjud)
cd ../eval/profile_fields
python -I final.py ../../agent/cache/profile_fields.json && python -I stats.py && python -I docs.py
```

Um einen Lieferanten neu zu extrahieren, seine Dateien in `out/*/` löschen. Für das Sonntags-Release: dessen CSV-Zeilen und Briefe in `bundle.py` mit `release: "sunday"` aufnehmen, `out/` löschen und alles neu laufen lassen.
