# Reflexe

## Kde prompt selhává

1. **Halucinace mimo vstup** — i se zákazem může slabší model doplnit ÚP/záplavy z tréninku. Mitigace: nízká teplota, validace polí, human-in-the-loop.
2. **Vizuální kontext jen textově** — mapy.cz model nevidí; „proluka“ neověří sjezd ani sítě.
3. **Cena a likvidita** — bez tržních dat je `skore_zajmu` kvalitativní.
4. **Zemědělství vs. rozvoj** — case 05 popíše konflikt ÚP/LPIS, ale nerozhodne zábor ZPF (`overit_rucne`).
5. **Kalibrace skóre** — prahy uvnitř sady drží, bez feedbacku Viagem jsou spekulativní.
6. **Enum drift** — menší modely vrátí anglické klíče; runner gate to částečně chytí.
7. **Referenční actuals** — bez API klíče nejsou `actual.json` nezávislým měřením modelu. Jsou to výstupy sestavené podle finálního promptu; live `--save-actual` je nahradí.

## Co bych s víc časem udělal jinak

- Read-only open data (AOPK WMS, záplavy, digitální ÚP) jako předvyplněný JSON — pořád bez ČÚZK API.
- Eval sada 20–30 parcel s anotací analytika Viagem.
- Dva módy skóre: stavební vs zemědělský výkup.
- Native JSON schema u poskytovatele.
- Krátký second pass jen na konflikty/chybějící data.

## Rozsah
Cíl 4–8 h. Po design breakdown doplněna iterace 7 (gate, case Q100, upřímnost). FABINI v kořeni nedotčen.
