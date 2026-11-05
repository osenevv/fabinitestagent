# Viagem — AI konzultant výkup parcel (offline showcase)

Interaktivní ukázka vyhodnocení parcel pro výkup. **Bez API klíčů a bez živých LLM volání** — příklady načítají fixture vstupy a předpočítané / branami ošetřené JSON výsledky.

## Spuštění

```bash
cd viagem-showcase
npm install
npm run dev
```

Otevřete URL z terminálu (výchozí `http://127.0.0.1:5173`).

Produkční build:

```bash
npm run build
npm run preview
```

## Co uvidíte

- Seznam suite i adversarial příkladů (záplava, NATURA, exekuce, injekce, chybějící data…)
- Tlačítko **Spustit** — krátká offline animace zpracování
- Doporučení, skóre, jistota, osy rizik, checklist
- U vybraných případů přepínač **Surový model / Po branách** a výpis zásahu deterministických bran

## Poznámka

Data jsou vestavěná v `src/data/catalog.json`. Aplikace nevolá žádnou síťovou LLM službu.
