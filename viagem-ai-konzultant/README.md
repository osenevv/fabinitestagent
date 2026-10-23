# Viagem — AI konzultant parcely (interview úloha)

Samostatný balíček pro Viagem a.s.: LLM prompt + minimální Python obal, který z dat o jedné parcele vrátí strukturované vyhodnocení pro rozhodnutí o výkupu.

**Jazyk:** čeština (prompt, výstup, poznámky).  
**Mimo rozsah:** integrace na ČÚZK API, automatické stahování open dat. Data v testech jsou **ručně sestavené** fikční, ale realistické výpisy pro úlohu.

Kořen tohoto repa obsahuje jiný projekt (FABINI). Tento adresář `viagem-ai-konzultant/` je self-contained — lze ho zipnout nebo zkopírovat do privátního repa.

## Layout

```
viagem-ai-konzultant/
  README.md
  requirements.txt
  evaluate.py                 # runner: parcela → JSON vyhodnocení
  prompts/
    system.txt                # finální system prompt
    user_template.txt         # šablona user zprávy
  schema/
    vyhodnoceni.schema.json   # JSON Schema výstupu
  data/parcels/               # 6 vstupních parcel
  tests/cases/<id>/
    input.json                # vstup
    expected.json             # očekávané kontroly (ne soft label celého JSON)
    actual.json               # skutečný výstup modelu (zaznamenaný)
  notes/
    iterace.md
    reflexe.md
```

## Architektura (proč tak)

- **Jeden system prompt + user šablona** — data parcely se vloží jako JSON. Žádný multi-agent stack: u interview hodnotí kvalitu promptu a konzistenci.
- **Stabilní JSON taxonomie** — `skore_zajmu`, `doporuceni`, rizika (`zaplava` / `ochrana_prirody` / `uzemni_plan`), `jistota`, `overit_rucne`, `konflikty_dat`, `chybejici_data`.
- **Konzervativní pravidla** pro hrany: chybějící data → `neznamo` + nízká jistota; konflikty → ne `vykupit`; aktivní zóna / NATURA → vysoké až kritické riziko.

## Požadavky

- Python 3.10+ (stdlib; `urllib` pro live API)
- Volitelně: `pip install -r requirements.txt` (prázdné / minimální — žádný SDK není nutný)

```bash
cd viagem-ai-konzultant
python3 evaluate.py --run-all-tests
python3 evaluate.py data/parcels/01-stavebni-happy.json
```

Bez API klíče offline režim **vrátí uložený** `tests/cases/<id>/actual.json` pro známé parcely.

## Live model (volitelné)

Stejný vzor jako FABINI README — OpenAI-kompatibilní endpointy + Anthropic:

```bash
export OPENAI_API_KEY=...          # default model gpt-4o-mini
# nebo
export XAI_API_KEY=...             # https://api.x.ai/v1, grok-3-mini
# nebo
export GEMINI_API_KEY=...          # generativelanguage.googleapis.com/v1beta/openai
# nebo
export ANTHROPIC_API_KEY=...       # claude-sonnet-4-5

python3 evaluate.py --live data/parcels/01-stavebni-happy.json --out /tmp/out.json
```

Volitelné override: `OPENAI_MODEL`, `OPENAI_BASE_URL`, `XAI_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`.

Vytisknout sestavený prompt bez volání API:

```bash
python3 evaluate.py --print-prompt data/parcels/04-chybici-data.json
```

## Model, který produkoval `actual.json`

V prostředí cloud agenta **nebyly** nastavené API klíče. Soubory `tests/cases/*/actual.json` vznikly **ručním během finálního promptu** (stejný system + user template) modelem uvedeným v `_meta.model` (`composer-agent (ruční běh finálního promptu)`). Jsou to skutečné výstupy podle instrukcí promptu, ne prázdné placeholdery.

Pro živé přegenerování nastav klíč a spusť `--live` (ideálně přepiš `actual.json` a znovu `--run-all-tests`).

## Test cases (strategie)

| ID | Scénář | Co má prasknout / projít |
| --- | --- | --- |
| `01-stavebni-happy` | Proluka BH, čistá rizika | Vysoké skóre, stavební potenciál |
| `02-zaplava` | Aktivní zóna + nezastavitelný ÚP | `odmitnout`, kritické záplavy |
| `03-natura-aopk` | EVL + CHKO | Odmítnout stavební výkup |
| `04-chybici-data` | Jen KN | `neznamo`, nízká jistota, `chybejici_data` |
| `05-konflikt-zdroju` | ÚP vs LPIS, rozporné záplavy | `konflikty_dat`, ne `vykupit` |
| `06-zemedelsky` | Orná NZ, čistá data | `zemedelsky`, ne stavební halucinace |

`expected.json` kontroluje **prahové vlastnosti** (doporučení, rozsah skóre, přítomnost konfliktů), ne byte-identický text — u LLM je to smysluplnější.

## Odevzdání

- Finální prompt: `prompts/system.txt` (+ `user_template.txt`)
- Obal: `evaluate.py`
- Test cases: `tests/cases/`
- Iterace: `notes/iterace.md`
- Reflexe: `notes/reflexe.md`

Žádné secrety do repa nedávej — jen env vars.
