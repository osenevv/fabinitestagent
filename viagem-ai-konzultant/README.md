# Viagem — AI konzultant parcely (interview úloha)

Samostatný balíček pro Viagem a.s.: LLM prompt + robustní Python obal (stdlib only), který z dat o jedné parcele vrátí strukturované vyhodnocení pro rozhodnutí o výkupu.

**Jazyk:** čeština (prompt, výstup, poznámky, kód).  
**Závislosti:** Python 3.10+ bez jakýchkoli externích pip knihoven (zero-dependency stdlib).  
**Mimo rozsah:** integrace na ČÚZK API a stahování open dat (dle zadání).

---

## 1. Architektura a principy návrhu

Řešení striktně rozděluje práci mezi dvě domény:

1. **Latentní prostor (LLM úsudek):**
   - Interpretace plánovacích, přírodních a právních limitů do standardizované taxonomie (`uroven`: `zadne`, `nizke`, `stredni`, `vysoke`, `kriticke`, `neznamo`).
   - Volba výkupní strategie (`hlavni_potencial`: `stavebni`, `zemedelsky`, `lesni`, `rekreacni`, `infrastrukturni`, `nejisty`, `nevyuzitelny`).
   - Odhad kvality využitelnosti (`vyuzitelnost`: 0–30 bodů dle lokality a regulativů).
   - Slovní shrnutí, detekce rozporů (`konflikty_dat`), chybějící údaje a checklist manuální prověrky (`overit_rucne`).

2. **Deterministický prostor (Kód & Politika):**
   - **Formální validace kontraktu:** Vlastní lehký validátor podmnožiny JSON Schema 2020-12 ([konzultant/schema.py](konzultant/schema.py)) ověřuje typy, enumy, rozsahy i `additionalProperties: false`.
   - **Výpočet skóre zájmu:** Skóre není stochastický odhad LLM, ale deterministický součet složek. Runner automaticky dopočítává bodové srážky za rizika a při kritickém riziku vynucuje strop 35 bodů ([konzultant/policy.py](konzultant/policy.py)).
   - **Brány po klasifikaci (ne „ochrana před modelem“):** Doporučení `vykupit` je algoritmicky zakázáno, pokud *už klasifikovaný* výstup obsahuje kritické riziko (včetně `rizika.pravni_stav` a `rizika.dalsi`), neprázdné `konflikty_dat`, jistotu &lt; 60 nebo skóre &lt; 70. Brány **nečtou** parcelní JSON samy — když model špatně označí Q100 jako `nizke` nebo po injekci vyprázdní konflikty, `vykupit` projít může. Spolehlivost na čerstvé parcele tedy stojí primárně na promptu + modelu; brány jen sjednocují konzistenci po klasifikaci.
   - **Samoopravný cyklus (Auto-repair):** Pokud model vygeneruje nevalidní JSON nebo poruší bránu, runner automaticky provede 1 opravný konverzační tah s výpisem konkrétních chyb.

---

## 2. Struktura balíčku

```
viagem-ai-konzultant/
  README.md
  requirements.txt            # volitelné (pro dev/test)
  evaluate.py                 # hlavní CLI runner
  prompts/
    system.txt                # finální system prompt (pravidla, taxonomie, brány)
    user_template.txt         # šablona uživatelské zprávy s oddělením dat
  schema/
    vyhodnoceni.schema.json   # formální JSON Schema kontrakt
  konzultant/                 # aplikační logika (stdlib)
    __init__.py
    schema.py                 # zero-dependency JSON Schema validátor
    policy.py                 # deterministická pravidla skóre a bezpečnostní brány
    inputs.py                 # loader JSON / volného textu a sanitizace
    pipeline.py               # orchestrace LLM volání a opravného cyklu
    providers.py              # adaptéry (Claude Code CLI, OpenAI, Anthropic, Gemini, xAI)
    checks.py                 # ověřování testovacích prahů expected.json
  tests/
    test_konzultant.py        # unit testy knihovny (16 testů, <0.01s)
    cases/                    # 12 testovacích scénářů (vstup, očekávání, výstup)
      01-stavebni-happy/      # stavební proluka BH (happy path)
      02-zaplava/             # aktivní zóna záplavového území
      03-natura-aopk/         # EVL NATURA 2000 + II. zóna CHKO
      04-chybici-data/        # neúplný KN vstup bez vrstev
      05-konflikt-zdroju/     # rozporné záplavy (konflikt) vs ZPF (běžný stav)
      06-zemedelsky/          # bonitní orná půda NZ
      07-q100-stavebni/       # Q100 bez aktivní zóny (stavební nuance)
      08-exekuce/             # exekuční příkaz k prodeji na LV (blokovaný převod)
      09-spoluvlastnicky-podil/# menšinový podíl 1/10 a zapsaný pacht
      10-bez-pristupu/        # vnitroblok bez přístupu z veřejné cesty
      11-prompt-injection/    # pokus o manipulaci promptu v poznámce KN
      12-volny-text/          # surový textový výpis z KN (kopírováno z webu)
  notes/
    iterace.md                # záznam designových a architektonických změn
    reflexe.md                # zhodnocení limitů a doporučení pro produkci
```

---

## 3. Rychlé spuštění (Quickstart)

### 3.1 Spuštění testovací sady (offline gate testy)
Nevyžaduje žádný API klíč, trvá pod 1 vteřinu:
```bash
python3 evaluate.py --run-all-tests
python3 -m unittest tests/test_konzultant.py
```

### 3.2 Spuštění vyhodnocení parcely
Skript automaticky detekuje dostupného poskytovatele (lokální Claude Code CLI `claude`, případně env klíče `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`):

```bash
# Vyhodnocení konkrétního souboru:
python3 evaluate.py tests/cases/01-stavebni-happy/input.json

# Zkouška čerstvé parcely ze schránky (volný text z KN):
pbpaste | python3 evaluate.py -

# Zobrazení sestaveného promptu:
python3 evaluate.py --print-prompt tests/cases/08-exekuce/input.json

# Offline přehrání uloženého referenčního výstupu:
python3 evaluate.py --offline tests/cases/01-stavebni-happy/input.json
```

---

## 4. Zadání čerstvé parcely na pohovoru

Zkoušející může předložit parcelu ve dvou podobách:

### A) Strukturovaný JSON
Stačí vytvořit JSON s dostupnými údaji (např. `moje_parcela.json`):
```json
{
  "ku": "Královo Pole",
  "cislo_parcely": "1520/2",
  "vymera_m2": 1200,
  "druh_pozemku": "zahrada",
  "lv": "412",
  "uzemni_plan": { "funkcni_plocha": "BI" },
  "dalsi": { "pristup": "místní komunikace" }
}
```

### B) Surový text z Nahlížení do KN nebo e-mailu
Skript i prompt přímo podporují nestrukturovaný text. Stačí zkopírovat výpis z webu ČÚZK do souboru nebo poslat přes stdin:
```bash
python3 evaluate.py vycuc_z_kn.txt
```

---

## 5. Provenance `actual.json` (důležité)

**Zelená offline sada ≠ změřený LLM.**

- Soubory `tests/cases/*/actual.json` jsou **referenční výstupy autora** sestavené podle finálního promptu a politiky (`_meta.zdroj: referencni`). **Nejsou** výsledkem live běhu GPT/Claude/Gemini/xAI.
- `python3 evaluate.py --run-all-tests` ověřuje shodu fixtures ↔ expected ↔ schéma/brány. **Neměří**, že produkční model prompt dodrží.
- `--offline` jen přehrává uložený `actual.json` a na stderr píše, že nejde o model.

### Stav live evaluace v tomto odevzdání

V prostředí, kde balíček vznikal, **nebyl dostupný žádný API klíč** (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `XAI_API_KEY`) ani použitelný Claude Code CLI pro měření. **Live eval proto neproběhl** — v `tests/results/` není report z reálného modelu. Viz také `notes/reflexe.md`.

### Jak spustit live (až bude klíč)

```bash
# Tvrdé cases z change-review (nebo celá sada bez --cases):
python3 evaluate.py --eval-live --cases 02-zaplava,05-konflikt-zdroju,07-q100-stavebni,08-exekuce,11-prompt-injection,12-volny-text \
  --runs 1 --threshold 0.85

# Celá sada + přepsat actuals reálnými výstupy:
python3 evaluate.py --eval-live --runs 3 --threshold 0.85 --save-actual
```

Report se uloží do `tests/results/eval-YYYYMMDD-HHMMSS.json`.
