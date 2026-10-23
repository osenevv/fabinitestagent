# Poznámky k iteracím promptu

## Iterace 0 — záměr
Cíl: jeden system prompt + JSON výstup pro rozhodnutí o výkupu (ne volný esej). Taxonomie: `skore_zajmu`, rizika (záplavy / ochrana / ÚP), využitelnost, jistota, `overit_rucne`.

## Iterace 1 — první draft
Prompt popisoval „napiš hodnocení parcely“. Výstup byl nekonzistentní: jednou odrážky, jindy JSON s jinými klíči (`score` vs `skore`).  
**Změna:** pevné schéma + „odpověz VÝHRADNĚ JSON“.

## Iterace 2 — chybějící data
Na case `04-chybici-data` model doplňoval ÚP a záplavy „z obecné znalosti“.  
**Změna:** tvrdé pravidlo „pracuj POUZE se vstupem“; chybějící → `chybejici_data` + `uroven: neznamo` + snížená jistota.

## Iterace 3 — konflikty
Na case `05-konflikt-zdroju` model průměroval ÚP BI a LPIS ornou do „spíš bydlení“ a dával `vykupit`.  
**Změna:** povinné `konflikty_dat`; při konfliktu zákaz `vykupit`; `hlavni_potencial: nejisty`.

## Iterace 4 — záplavy a NATURA
`02-zaplava` někdy dostalo jen „střední riziko“ i při aktivní zóně. `03-natura` skórovalo zemědělský TTP příliš vysoko.  
**Změna:** speciální pravidla — aktivní zóna → `vysoke`/`kriticke`; EVL/CHKO pro stavební výkup → `kriticke`; zemědělství posuzovat zvlášť, ale limity ochrany držet skóre nízko.

## Iterace 5 — doporučení vs jistota
Happy path občas `zkoumat_dal` i při čistých datech, nebo naopak `vykupit` při jistotě 30.  
**Změna:** orientační prahy v promptu + preferovat konzervativní doporučení při nízké jistotě; `overit_rucne` vždy konkrétní body.

## Iterace 6 — finální balíček
Zafixováno schéma v `schema/vyhodnoceni.schema.json`, system prompt v `prompts/system.txt`, user šablona, runner a 6 test cases s `actual.json` z ručního běhu finálního promptu (bez API klíče v prostředí).

## Co by byla další iterace (neuděláno)
- Few-shot příklad uvnitř promptu (1× dobrý JSON) pro stabilitu menších modelů
- Striktní JSON Schema validace přes `jsonschema` při live běhu
- Kalibrace skóre na interních historických výkupech Viagem
