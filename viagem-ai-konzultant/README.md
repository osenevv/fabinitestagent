# Viagem — AI konzultant parcely

Prompt + stdlib runner: data o jedné parcele → JSON vyhodnocení pro výkup.

Čeština. Python 3.10+, bez pip závislostí. Bez integrace na ČÚZK API.

Cloud API klíč nebyl k dispozici. Hard cases `02, 05, 07, 08, 11, 12` mají `actual.json` ze stejného promptu přes interní LLM + runner (`_meta.zdroj: live-agent-llm`). Ostatní cases jsou `referencni`.

## Architektura

- **LLM:** úrovně rizik, potenciál, jistota, texty, konflikty.
- **Kód:** JSON Schema validace, dopočet skóre, strop 35 při `kriticke`, brány pro `vykupit`, 1 opravný tah.

## Struktura

```
prompts/           system + user šablona
schema/            vyhodnoceni.schema.json
konzultant/        domain, schema, policy, pipeline, providers, checks, harness, inputs
evaluate.py        CLI
data/parcels/      demo vstupy
tests/cases/       12× input + expected + actual
tests/             unit + mock HTTP
notes/             iterace, reflexe
notebooks/         offline ukázka
scripts/           lokální OpenAI stub
```

## Spuštění

```bash
python3 evaluate.py --run-all-tests
python3 -m unittest tests.test_konzultant tests.test_live_http

python3 evaluate.py tests/cases/01-stavebni-happy/input.json
python3 evaluate.py --offline tests/cases/01-stavebni-happy/input.json
python3 evaluate.py --print-prompt tests/cases/08-exekuce/input.json
pbpaste | python3 evaluate.py -
```

Poskytovatel: Claude Code CLI, nebo `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `XAI_API_KEY`.

```bash
python3 evaluate.py --eval-live --save-actual --cases 02-zaplava,08-exekuce
```

Mock HTTP: `python3 -m unittest tests.test_live_http`.  
Lokální stub: `scripts/local_openai_stub.py` + `OPENAI_BASE_URL=http://127.0.0.1:8765/v1`.

## Čerstvá parcela

JSON se známými poli, nebo surový text z KN / e-mailu:

```bash
python3 evaluate.py moje_parcela.json
python3 evaluate.py vycuc_z_kn.txt
```

## Notebook

```bash
jupyter notebook notebooks/vyhodnoceni.ipynb
```
