# Reflexe

## Limity

1. Bez GIS geometrie nejde ověřit tvar pozemku ani uliční čáru — jen slovní popis.
2. Bez cenových map není ocenění v Kč; výstup je kvalitativní.
3. Nezapsaný pacht z veřejného KN často neplyne.
4. Volný text: neúplnost vs. absence rizika nejde spolehlivě rozlišit; jistota max 60.
5. Váhy skóre drží konzistenci, ale nejsou kalibrované na historických výkupech Viagemu.

## S více časem

- Předvyplnění z ČÚZK / DIBAVOD / AOPK / LPIS.
- Ortofoto do multi-modálního modelu.
- Oddělené skóre pro stavební vs. zemědělský výkup.
- CI `--eval-live` na větší sadě parcel.

## Provenance actuals

Cloud API klíč nebyl. Hard cases 02, 05, 07, 08, 11, 12: interní LLM + stejný runner (`live-agent-llm`). Ostatní: `referencni`. Report: `tests/results/eval-agent-llm-hard-cases.json`.

## Rozsah

V limitu 4–8 h je i schema, policy, validátor, multi-provider runner a 12 cases, nejen ladění promptu.
