# Draft schema: structured supplier profile fields (v0)

Purpose: a buyer negotiation agent (Parley) negotiates price changes with 46 industrial suppliers. Free-text
profiles exist (LLM-written summaries). We want STRUCTURED, VERIFIABLE fields the deterministic policy can use.
Extraction is strictly from the company's own data (call/meeting notes, e-mails, letters, news, contracts,
quotes, negotiation-history notes, register data). No outcome data. Every non-null field needs evidence:
source id + verbatim quote that must exist in that source (checked programmatically). No evidence -> null.

Per supplier, each field is {"value": ..., "evidence": [{"source": "<unit id>", "quote": "<verbatim>"}],
"confidence": "high|medium|low"}.

| field | type | meaning |
|---|---|---|
| approval_threshold_pct | number or null | price change size above which the supplier rep needs internal approval (e.g. committee) |
| approval_cycle | text or null | how often / who approves (e.g. "pricing committee every two weeks") |
| preferred_levers | list of enum | non-price terms the supplier signals it values or is open to trade |
| avoid_levers | list of enum | terms the supplier signals it rejects or that irritate it |
| capacity_tight | bool or null | supplier is capacity-constrained (sold out, allocation, high utilisation) |
| price_freeze | {"value": bool, "until": "YYYY-MM" or null} | supplier states no individual price reductions (head-office freeze, list prices for all) |
| cash_need | none / weak / strong / null | how much the supplier needs liquidity (asks for faster payment, bank watching, short-time work) |
| hedged_inputs | list of index series ids | input costs the supplier has bought forward / covered at fixed prices (so citing that index fall will not work) |
| uninformed_owner | bool or null | owner does not track own cost/market prices (old spreadsheet, did not follow prices) |
| decision_maker | owner / kam / inside_sales / export / null | who negotiates: owner personally, key account manager of a group, distributor inside sales, export sales of a foreign plant |
| patience | low / med / high / null | tolerance for many rounds |

Lever enum (direction is always relative to the buyer's offer package):
- payment_days_short: buyer pays faster than standard (supplier gets cash sooner, e.g. 10 days for a cash discount)
- payment_days_long: buyer pays later than standard
- lead_time_slack: buyer accepts longer lead time / call-off flexibility
- contract_months_longer: longer contract term
- volume_commitment: binding volume or quota commitment
- index_clause: price linked to a material/energy index
- effective_date_later: (buyer offers to accept a price change later / supplier accepts later effective date)
- ppm_relaxed: buyer relaxes quality target
- chargeback_none: buyer waives the flat EUR 150 per complaint fee
- tooling: buyer pays or co-funds tooling

Index series ids available: PPI-C2451 casting iron, PPI-C2452 casting steel, PPI-C2562 machining, PPI-C2511 metal
structures, PPI-C2594 fasteners, PPI-C2711 electric motors, PPI-C2732 wires/cables, PPI-C2611 electronic components,
PPI-C2612 loaded boards, PPI-C2815 bearings/gears, PPI-C241 basic iron and steel, PPI-C2431 cold drawn bars,
PPI-C2219 rubber products, PPI-C2813 pumps, PPI-D351 electric power, PPI-D35 electricity/gas, LCI-C labour,
WB-CU copper, WB-NI nickel, WB-AL aluminium, WB-NGEU natural gas, WB-FEORE iron ore, ECB-* FX, SMARD-DA day-ahead power,
CU-NOTATION copper notation, AS-14404 / AS-14301 alloy surcharges.

How the policy would use them (sketch): per-round price step <= approval threshold; offer packages built from
preferred levers; never offer avoid levers; capacity_tight -> trade lead-time slack / commitment instead of price;
cash_need strong -> lead with short payment; hedged inputs -> do not cite those indices; price_freeze -> negotiate
terms not price; uninformed_owner -> cite more verified facts per message; patience low -> fewer rounds, earlier
final offer.
