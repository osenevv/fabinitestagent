# Live eval — cloud API neběžel; hard cases přes interní LLM

**Placené cloud API** (OpenAI / Anthropic / Gemini / xAI) v prostředí odevzdání **nebylo** — klíč byl jen lokální stub (`sk-loca…` → `127.0.0.1:8765`), `api.openai.com` vrátil 401.

## Co proběhlo místo toho

Hard cases **02, 05, 07, 08, 11, 12** byly vyhodnoceny **interním LLM Cloud Agentu** nad stejným `prompts/system.txt` + user šablonou, výstup prošel stejným `schema` + `policy` runnerem jako `evaluate.py`.

- `_meta.zdroj: live-agent-llm`
- `_meta.poskytovatel: cursor-agent`
- report: `tests/results/eval-agent-llm-*.json`

Ostatní cases zůstávají `_meta.zdroj: referencni` (autor podle promptu).

Až bude cloud klíč: `python3 evaluate.py --eval-live --save-actual --provider openai` (bez stub `OPENAI_BASE_URL`).
