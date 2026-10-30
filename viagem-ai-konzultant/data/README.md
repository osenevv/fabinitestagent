# Demo parcely vs. test cases

- **`data/parcels/`** — čisté vstupy pro CLI / pohovor (`python3 evaluate.py data/parcels/….json`). Bez `expected` / `actual`.
- **`tests/cases/<id>/`** — vyhodnocovací harness: `input.(json|txt)`, `expected.json`, `actual.json`.

Stejné scénáře sdílejí id (`01-stavebni-happy`, …), ale **nesdílejí soubory** — `input` v cases je záměrně kopie (ne symlink), aby zip odevzdání fungoval i bez `data/`. Načtení dema: `inputs.load_demo_parcel(id)`.
