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
  data/parcels/               # vstupní parcely
  tests/cases/<id>/
    input.json                # vstup
    expected.json             # prahové kontroly
    actual.json               # zaznamenaný / referenční výstup
  notes/
    iterace.md
    reflexe.md
```

## Architektura (proč tak)

- **Jeden system prompt + user šablona** — žádný multi-agent; na pohovoru rozhoduje kvalita promptu.
- **Stabilní JSON** — skóre, doporučení, rizika (záplavy / ochrana / ÚP), využitelnost, jistota, `overit_rucne`, konflikty, chybějící data.
- **Tvrdé gate** — `kriticke` riziko, neprázdné konflikty nebo jistota &lt; 40 ⇒ zákaz `vykupit` (v promptu i ve validaci runneru).

## Minimální vstup (čerstvá parcela)

Stačí JSON; chybějící vrstvy model musí označit jako `neznamo`, ne vymýšlet:

```json
{
  "id": "moje-parcela",
  "ku": "…",
  "cislo_parcely": "…",
  "vymera_m2": 0,
  "druh_pozemku": "…",
  "kn_poznamky": "…",
  "uzemni_plan": null,
  "zaplavove_uzemi": null,
  "ochrana_prirody": null,
  "lpis": null,
  "vizualni_kontext": null,
  "dalsi": {}
}
```

Vzory: `data/parcels/*.json`.

## Požadavky

- Python 3.10+ (stdlib)
- Volitelně `pip install -r requirements.txt` (žádný SDK nutný)

```bash
cd viagem-ai-konzultant
python3 evaluate.py --run-all-tests
python3 evaluate.py data/parcels/01-stavebni-happy.json
```

**Bez `--live`:** skript vrátí uložený `tests/cases/<id>/actual.json` a na stderr napíše, že nejde o live model.

## Live model

```bash
export OPENAI_API_KEY=...          # default gpt-4o-mini
# nebo XAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY

python3 evaluate.py --live data/parcels/01-stavebni-happy.json --out /tmp/out.json
python3 evaluate.py --live --save-actual data/parcels/07-q100-stavebni.json
python3 evaluate.py --print-prompt data/parcels/04-chybici-data.json
```

Override: `OPENAI_MODEL`, `OPENAI_BASE_URL`, `XAI_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`.

## Provenance `actual.json`

V prostředí cloud agenta **nebyly** API klíče. Soubory `actual.json` jsou **referenční výstupy autora** sestavené podle finálního `prompts/system.txt` (ne nezávislý běh GPT/Claude). V `_meta` je to uvedené. Pro měření reálného modelu: `--live --save-actual` a znovu `--run-all-tests`.

## Test cases

| ID | Scénář | Očekávání |
| --- | --- | --- |
| `01-stavebni-happy` | Proluka BH, čistá rizika | `vykupit`, stavební |
| `02-zaplava` | Aktivní zóna + nezastavitelný ÚP | `odmitnout`, kritické záplavy |
| `03-natura-aopk` | EVL + CHKO | odmítnout stavební výkup |
| `04-chybici-data` | Jen KN | `neznamo`, nízká jistota |
| `05-konflikt-zdroju` | ÚP vs LPIS, rozporné záplavy | konflikty, ne `vykupit` |
| `06-zemedelsky` | Orná NZ | `zemedelsky` |
| `07-q100-stavebni` | BI + Q100 mimo aktivní zónu | `zkoumat_dal`/`odlozit`, záplava ≥ vysoké |

`expected.json` = prahy (doporučení, rozsah skóre, konflikty), ne byte-identický text.

## Odevzdání

1. Prompt: `prompts/system.txt` (+ `user_template.txt`)
2. Obal: `evaluate.py`
3. Test cases: `tests/cases/`
4. Iterace: `notes/iterace.md`
5. Reflexe: `notes/reflexe.md`

Žádné secrety do repa — jen env vars.
