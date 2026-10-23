# Poznámky k iteracím promptu

> **Upřímnost:** Iterace níže jsou **design-time** — odvozené při psaní promptu a test fixtures, ne ze zaznamenaných API logů. V prostředí nebyl API klíč; `actual.json` jsou referenční výstupy autora podle finálního promptu. Live přegenerování: `python evaluate.py --live --save-actual …`.

## Iterace 0 — záměr
Jeden system prompt + JSON pro výkup (ne esej). Taxonomie: `skore_zajmu`, rizika, využitelnost, jistota, `overit_rucne`.

## Iterace 1 — stabilní tvar
Volný text by kolísal (`score` vs `skore`, odrážky vs JSON).  
**Změna:** pevné schéma + „odpověz VÝHRADNĚ JSON“.

## Iterace 2 — anti-halucinace
Při neúplném vstupu (case 04) hrozí doplňování ÚP/záplav z „obecné znalosti“.  
**Změna:** „pracuj POUZE se vstupem“; chybějící → `chybejici_data` + `neznamo` + nižší jistota.

## Iterace 3 — konflikty
Při ÚP BI vs LPIS orná (case 05) hrozí průměrování do `vykupit`.  
**Změna:** povinné `konflikty_dat`; zákaz `vykupit` při konfliktu; `hlavni_potencial: nejisty`.

## Iterace 4 — záplavy a NATURA
Aktivní zóna nesmí skončit jako „střední“. NATURA/CHKO nesmí dostat vysoké skóre jako stavební kandidát.  
**Změna:** speciální pravidla + později **tvrdé gate** (`kriticke` ⇒ ne `vykupit`).

## Iterace 5 — doporučení vs jistota
`vykupit` při nízké jistotě je nebezpečné.  
**Změna:** orientační prahy + gate `jistota < 40` ⇒ ne `vykupit`; konkrétní `overit_rucne`.

## Iterace 6 — balíček v1
Schéma, runner, 6 cases, README.

## Iterace 7 — po design breakdown (tato revize)
Nalezené díry: chybějící tvrdé gate, Q100 ≠ aktivní zóna, volné expected, neupřímné actual/`_meta`, offline bez varování, `konflikty_dat`/`chybejici_data` nepovinné ve schématu.  
**Změna:** tvrdé gate v promptu i runneru; case `07-q100-stavebni`; utažení expected; `--save-actual`; offline stderr; povinná pole ve schématu; upřímný wording provenance.

## Další (neuděláno)
- 1× few-shot JSON v promptu pro menší modely
- Native structured output u poskytovatele
- Kalibrace skóre na reálných výkupech Viagem
