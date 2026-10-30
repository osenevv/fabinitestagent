# Iterace promptu

Iterace 0–7 vznikly při návrhu promptu a fixtures. Od 8 jde o běžící balíček.

## 0 — záměr
Jeden system prompt + JSON pro výkup. Pole: skóre, rizika, využitelnost, jistota, `overit_rucne`.

## 1 — stabilní tvar
Volný text kolísal. Pevné schéma, výstup jen JSON.

## 2 — anti-halucinace
Neúplný vstup doplňoval vrstvy. Pracuj jen se vstupem; chybějící → `chybejici_data` / `neznamo`.

## 3 — konflikty
ÚP BI vs LPIS orná se průměrovalo do `vykupit`. Povinné `konflikty_dat`, zákaz `vykupit` při konfliktu.

## 4 — záplavy a NATURA
Aktivní zóna a NATURA/CHKO nesmí skončit jako střední riziko. Pravidla + gate na `kriticke`.

## 5 — jistota
`vykupit` při nízké jistotě. Prahy jistoty + konkrétní `overit_rucne`.

## 6 — balíček v1
Schéma, runner, první sada cases, README.

## 7 — po review
Tvrdé gate v promptu i runneru, case Q100, utažené expected, `--save-actual`, povinná `konflikty_dat` / `chybejici_data`.

## 8 — redesign
- Osa `pravni_stav` a `pristup`, struktura `vlastnictvi`.
- Deterministické skóre a strop 35 v `policy`.
- Stdlib JSON Schema validátor, 2-tahová oprava.
- `<parcela_data>` + sanitizace; volný text.
- Cases 08–12, unit testy.

## 9 — úklid
`domain.py` jako jediný zdroj konstant, `harness` pro offline hranici, tenčí CLI, parita enumů se schématem.
