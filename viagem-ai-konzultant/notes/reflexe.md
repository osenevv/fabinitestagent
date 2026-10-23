# Reflexe

## Kde prompt a systém naráží na limity

1. **Vizuální a prostorový kontext bez GIS/geometrie** — model pracuje pouze se slovním popisem polohy („proluka“, „vedle silnice“). Bez skutečných polygonů z katastrální mapy nedokáže ověřit uliční čáru, přesnou šířku a geometrii (např. pozemek o výměře 1 000 m², ale šířce 1,5 metru a délce 660 m je pro zástavbu nepoužitelný).
2. **Tržní ocenění a ekonomika** — model správně odmítá vymýšlet ceny v Kč, protože nemá přístup k cenovým mapám transakčních cen ČÚZK. Výstup je proto čistě kvalitativní; pro finální rozhodnutí analytika Viagemu je nutné napojení na cenovou mapu a kalkulačku marže.
3. **Zemědělský pacht a výnosnost** — u orné půdy určuje reálný peněžní tok výše pachtovného a délka výpovědní lhůty pachtu. Z veřejného KN je pacht zřejmý jen tehdy, je-li zapsán v oddílu C; většina pachtů se však do KN nezapisuje.
4. **Zranitelnost nestrukturovaného volného textu** — při vstupu formou volného textu (např. e-mail makléře) je jistota modelu správně omezena na max 60, protože neúplnost textu nelze jednoznačně odlišit od absence rizika.
5. **Kalibrace bodové rubriky na reálných datech** — bodové váhy (30 vyuzitelnost, 30 rizika, 20 právní stav, 20 přístup) drží konzistenci a korelují s riziky, ale pro produkční nasazení by měly být zkalibrovány na historických úspěšných i neúspěšných výkupech Viagemu.

## Co bych s více časem řešil v produkci

- **Automatické předvyplnění z veřejných API:**
  - ČÚZK WFS / Katastr API (automatické stažení parcelního čísla, druhu, způsobu využití, LV).
  - DIBAVOD WFS (geometrický průnik parcely se zónou Q100 a aktivní zónou bez lidské chyby).
  - AOPK ČR Open Data (přesný průnik s vrstvou maloplošných a velkoplošných ZCHÚ a NATURA 2000).
  - LPIS veřejný registr (bloky půdy, erozní ohroženost, bonita BPEJ).
- **Multi-modální vrstva (Vize nad ortofotomapou):**
  - Předání satelitního a katastrálního snímku z Mapy.cz přímo do multi-modálního modelu pro kontrolu přístupových cest, sjezdů a reálného porostu / černých staveb.
- **Dva specializované scoringové moduly:**
  - Samostatná sub-metrika pro developerský výkup (index zastavitelnosti, koeficient zeleně, kapacita sítí) vs. zemědělský výkup (bonita BPEJ, nájemní výnos, scelovací potenciál).
- **Automatizovaný noční eval (CI/CD):**
  - Pravidelný běh `--eval-live` nad sadou 50+ reálných parcel v CI/CD pipeline sledující drift kvality nově vydaných verzí modelů.

## Rozsah práce
Celkový rozsah: kompletní návrh promptu, formalizace schématu, implementace zero-dependency runneru, rozšíření na 12 edge-case scénářů a sada unit testů splňuje zadání v doporučeném rámci 4–8 hodin.
