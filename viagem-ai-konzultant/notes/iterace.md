# Poznámky k iteracím promptu

> **Upřímnost:** Iterace 0–7 byly design-time odvozené při psaní promptu a test fixtures. Iterace 8 přináší hlubokou architektonickou revizi, deterministickou politiku a rozšíření sady na 12 případů.

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

## Iterace 7 — po prvním design breakdownu
Nalezené díry: chybějící tvrdé gate, Q100 ≠ aktivní zóna, volné expected, neupřímné actual/`_meta`, offline bez varování, `konflikty_dat`/`chybejici_data` nepovinné ve schématu.  
**Změna:** tvrdé gate v promptu i runneru; case `07-q100-stavebni`; utažení expected; `--save-actual`; offline stderr; povinná pole ve schématu; upřímný wording provenance.

## Iterace 8 — hluboký architektonický audit a redesign v2 (finální)
Důkladný audit odhalil zásadní doménové a strukturální slabiny:
1. **Díra v bráně pro právní vady:** Exekuce, věcná břemena a zástavy končily v `rizika.dalsi`, které brána nekontrolovala — hrozilo doporučení `vykupit` parcelu s exekučním příkazem k prodeji.
2. **Ignorování vlastnické struktury a přístupu:** Chyběly klíčové osy `vlastnictvi` (1/1 vs menšinový podíl) a `pristup` (veřejná cesta vs vnitroblok).
3. **Mrtvé schéma:** `SCHEMA_PATH` se v kódu vůbec nepoužívala; runner měl pouze ad-hoc kontrolu.
4. **Stochastické skóre:** LLM hádalo jedno globální číslo bez explicitní rubriky a míchalo stavební a zemědělské výkupy.
5. **Nulová ochrana proti prompt injection a nestrukturovanému vstupu:** Zkoušející mohl zadat surový text z KN nebo záškodnický pokyn v poznámce.

**Změny v iteraci 8:**
- **Nový doménový datový model a schéma:** Pět povinných os rizik (`zaplava`, `ochrana_prirody`, `uzemni_plan`, `pristup`, `pravni_stav`), struktura `vlastnictvi` (typ, podíl, počet vlastníků), oprava schválených vlastností (`zpusob_vyuziti`, `lv`).
- **Rozdělení latentní vs. deterministický prostor:** LLM provádí kvalitativní úsudek a klasifikaci; modul `konzultant.policy` deterministicky počítá skóre zájmu dle bodové rubriky a vynucuje strop 35 bodů při jakémkoliv kritickém riziku.
- **Zero-dependency JSON Schema validátor:** Vlastní validátor podmnožiny JSON Schema 2020-12 v `konzultant/schema.py` bez nutnosti externích balíčků.
- **Samoopravný 2-tahový cyklus:** Při chybě validace model dostane přesný výpis chyb a 1 opravný tah.
- **Odolnost proti injekci a volnému textu:** Obalení dat do `<parcela_data>` s neutralizací uzavíracích značek; přímá podpora nestrukturovaného textu z KN a stdin.
- **Rozšíření evaluační sady z 7 na 12 případů:**
  - `08-exekuce` (exekuční příkaz k prodeji → blokace)
  - `09-spoluvlastnicky-podil` (podíl 1/10 + pacht)
  - `10-bez-pristupu` (vnitroblok bez cesty)
  - `11-prompt-injection` (pokus o manipulaci v poznámce)
  - `12-volny-text` (reálný zkopírovaný text z Nahlížení do KN)
- **Unit test suite:** 16 automatizovaných testů v `tests/test_konzultant.py` běžících pod 0.01 s.
