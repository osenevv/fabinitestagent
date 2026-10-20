# FABINI Operations Agent

Agent vezme provozní dotaz na objednávku, dočte Shopify, platbu, n8n log a WMS, a mutaci (`retry_sync`, `create_ticket`) pustí jen po schválení. Závěr je jeden JSON report: příčina, SOP, věta pro zákaznickou péči a audit každého kroku.

`python main.py` je offline a deterministický. Časy v `examples/` jsou zastavené, dva běhy vyrobí stejný diff.

## Spuštění

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests -v
```

`tests/test_evals.py` skóruje zadání: víc kroků, chyba neshodí proces, smyčka se zastaví, rizikový nástroj čeká na člověka, report má stopu.

Jeden scénář: `python main.py 100245-zamitnuto`.

Live tool calling, pořád stejná brána, model jen navrhne další nástroj:

```bash
export XAI_API_KEY=...          # https://api.x.ai/v1, grok-3-mini
# nebo OPENAI_API_KEY=...       # https://api.openai.com/v1, gpt-4o-mini
# nebo GEMINI_API_KEY=...       # generativelanguage.googleapis.com/v1beta/openai, gemini-2.5-flash
python main.py --live
```

Bez `--live` se klíč nečte. Ukázkové JSON se tím nepřepíšou. `--interactive` se na akci zeptá `y/n` a JSON neukládá.

Docker, offline ukázka:

```bash
docker build -t fabini-ops-agent .
docker run --rm fabini-ops-agent
```

Webhook pro n8n. Stav (zápisy a čekající schválení) zůstane ve svazku:

```bash
docker run --rm -p 8080:8080 -e FABINI_STATE_DIR=/data -v fabini-ops:/data \
  fabini-ops-agent python main.py --serve
```

`POST /hooks/n8n` bere stejné JSON jako `handle_n8n_webhook`. `GET /health`, `GET /approvals`, `GET /runs`. Live model v tom samém serveru: přidej `-e XAI_API_KEY=...` a `--live` na konec příkazu. Bez `--live` server plánuje pravidly, klíč nečte.

Obraz je `python:3.12-slim`. Build spustí unittest. `CMD` je `python main.py`.

## Architektura

Planner a brána jsou oddělené. Planner smí být `offline_rule_planner` nebo `LiveLLMPlanner`. Oba vrací `PlannerAction`. Spustit nástroj umí jen `_dispatch`.

`LiveLLMPlanner` posílá `POST /v1/chat/completions` přes `urllib` se schématy v `tools` a `tool_choice="auto"`. Z odpovědi vezme `tool_calls` a předá je bráně. Model tedy nástroj nespustí, ani když by SDK automatic function calling jinak zavolalo Python funkci samo. Když model skončí bez tool call, text se parsuje na `FinalLLMDecision`. Když status nesedí s fakty (404, zamítnutí, limit kroků, úspěšný retry), vyhrává `diagnose()`.

`diagnose(session)` je jediné místo, které píše příčinu, SOP a větu pro zákazníka. Offline planner jen vybírá další čtení nebo mutaci. Live planner vezme z jedné odpovědi jen první `tool_call` a další kolo uvidí jeho výsledek.

`OpsState` je paměť mezi webhooky, soubor `state.json` ve `FABINI_STATE_DIR` (jinak `var/`). Úspěšný `retry_sync` a `create_ticket` se zapíšou pod klíč `shopify_order_gid|UTC den|nástroj`. Další doručení téhož webhooku vrátí uložený výsledek a Odoo ani incident znovu neotevře. Když akce čeká, payload má `approval_id` a hotové tělo `resume` / `reject`. Druhé POST se stejným id dokončí tu akci, nebo vrátí už uloženou odpověď. `runs.jsonl` je krátká stopa každého běhu, čte ji `GET /runs`.

Brána v `_dispatch`:

- Čtení (`get_order`, `get_payment`, `get_fulfillment`, `get_sync_logs`) jde rovnou.
- Číslo v argumentech musí být přesně to ze zadání. Jiné, prázdné, obalené mezerami, nebo úplně chybějící číslo v zadání skončí `ORDER_SCOPE` a schválení se neotevře. Další klíč v argumentech stejný retry neobejde.
- `intent="propose"`, nebo věta bez slovesa typu oprav / zařiď / zopakuj / vyřeš, mutaci zastaví dřív, než se otevře schválení.
- Rizikový nástroj má v auditu `HUMAN_APPROVED`, `HUMAN_REJECTED`, `AWAITING_APPROVAL`, nebo `BLOCKED_BY_GUARDRAIL`. Sám se nespustí.
- Stejné argumenty podruhé: `DUPLICATE_CALL_BLOCKED`.
- `retry_sync` jen po `get_payment` se stavem `PAID`, jednou za objednávku, a to až když nástroj vrátí `OK`. Návrat 503 jde do `failed_retry_attempts` a vede na incident, ne do počítadla úspěšných retry.
- Výjimka z WMS (100500, interní retry, pak timeout) proces neshodí. Do stopy spadne JSON.
- Po šestém kroku smyčka končí. Když už čtyři čtení diagnózu složila, status zůstane ta diagnóza, ne slepé `ESCALATED`.

Každá relace má vlastní `MockSystems`. Dva agenti vedle sebe nesdílí zápis do expedice.

## Shopify, n8n, Odoo, zákaznická péče

Mock odpovídá tomu, co u vás teče v provozu. `get_order("100245")` vrací `store: fabini.cz`, `shopify_order_gid`, SKU `FAB-LIT-PEK-26` (Litinový pekáč s poklicí 26 cm) a tag `FABINI_GARANCE`. 100246 je `fabini.sk`. `get_sync_logs` u 100245 je spadlý n8n flow `shopify-order-paid-to-odoo`, kód `ODOO_ERP_SYNC_TIMEOUT_504`.

`handle_n8n_webhook` bere payload z automatizace:

```json
{"prompt": "...", "intent": "auto|propose|execute", "operator_approved": true}
```

`operator_approved: null` akci nespustí a vrátí `approval_webhook_payload` pro Slack nebo Google Chat (`#ops-approvals`, tlačítka approve/reject). `true` ji provede, `false` ji zapíše jako `HUMAN_REJECTED`.

Report má `sop_reference` (`SOP-OPS-01` retry Shopify do Odoo, `SOP-FIN-02` převod do tří dnů, `SOP-IT-09` výpadek WMS na L2) a `customer_care_suggestion`. Ta věta je pro zákazníka: co se děje s jeho nádobím, bez kódů, bez Odoo a bez n8n.

## Scénáře

| Soubor | Co se stane | Status |
| --- | --- | --- |
| `examples/100245-navrh.json` | PAID, sklad `NOT_FOUND`, n8n timeout. Retry se nespustí. | `PROPOSED_ACTION` |
| `examples/100245-retry.json` | Operátor schválí retry. Vznikne `FUL-998`. | `RESOLVED` |
| `examples/100245-zamitnuto.json` | Operátor retry zamítne. Sklad se nezmění. | `PROPOSED_ACTION`, ve stopě `HUMAN_REJECTED` |
| `examples/100245-ceka.json` | Schválení se odloží. | `AWAITING_APPROVAL` |
| `examples/100246-platba.json` | Převod `UNPAID_AWAITING_BANK_TRANSFER`, sync je čistý. | `PROPOSED_ACTION` |
| `examples/100500-wms.json` | WMS po retry timeoutuje, po schválení vznikne `INC-2026-0891`. | `ESCALATED` |
| `examples/999999-nenalezeno.json` | Shopify vrátí 404, dál se nevolá nic. | `FAILED` |

## Co je schválně mimo tenhle proces

Schvalovací tlačítko ve Slacku nebo Google Chatu jen pošle zpátky `resume` nebo `reject` na `/hooks/n8n`. Tenhle proces na klikání nečeká.

Shopify, Odoo a n8n jsou v ukázce mock. Až se napojí naživo, klíč zápisu zůstane stejný: GID a UTC den se kontrolují před zápisem do Odoo.

`runs.jsonl` stačí na to, aby šlo vidět status, SOP a jestli šlo o opakovaný zápis. Tokeny a latence poskytovatele sem nepatří.
