# Reflexe

## Kde prompt selhává

1. **Halucinace mimo vstup** — i se zákazem může slabší model „doplnit“ ÚP nebo záplavy z trénovacích dat. Mitigace: nízká teplota, JSON schema, kontrola `chybejici_data` v runneru; u produkce raději human-in-the-loop.
2. **Vizuální kontext jen textově** — mapy.cz / ortofoto neumí model opravdu „vidět“. Textová poznámka „proluka“ neověří sjezd, sítě ani svažitost.
3. **Cena a likvidita** — bez tržních dat je `skore_zajmu` kvalitativní. Dva pozemky se stejným ÚP můžou mít diametrálně jinou ekonomiku výkupu.
4. **Hrana zemědělství vs. rozvoj** — case 05 ukazuje, že konflikt ÚP/LPIS model popíše, ale nerozhodne právní cestu (zábor ZPF). To má zůstat na člověku (`overit_rucne`).
5. **Kalibrace skóre** — 82 vs 62 je konzistentní uvnitř této sady, ale bez feedbacku z reálných výkupů Viagem jsou prahy spekulativní.
6. **Jazyk a enum drift** — menší open-source modely občas vrátí `flood: high` místo `zaplava.uroven: vysoke`. Proto je v balíčku lehká validace + preferované silnější chat modely.

## Co bych s víc časem udělal jinak

- Napojit **read-only** open data (WMS AOPK, záplavy, ÚP kde je digitálně) jako předvyplněný JSON — pořád bez ČÚZK API, ale méně ruční práce.
- Přidat **eval sadu 20–30** parcel s anotací od analytika Viagem a měřit agreement na `doporuceni` / rizika.
- Oddělit **dva módy**: „výkup stavební“ vs „výkup zemědělský“ (jiná váha skóre).
- Structured output přes native JSON schema u poskytovatele (OpenAI/Anthropic) místo doufání v `response_format`.
- Krátký **second pass**: critique agent jen na konflikty a chybějící data (pořád jednoduché, ne multi-agent orchestrace).

## Rozsah času
Odhad zadání 4–8 h. Tento balíček cílí na kvalitu promptu, iterací a hraničních cases — ne na GIS pipeline. FABINI agent v kořeni repa je nedotčený.
