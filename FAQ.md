<a name="top"></a>
# Întrebări frecvente (FAQ) — OPCOM România

- [Ce este OPCOM?](#ce-este-opcom)
- [De ce aș folosi această integrare?](#de-ce-aș-folosi-această-integrare)
- [Când se publică prețurile de mâine?](#când-se-publică-prețurile-de-mâine)
- [Ce unitate de măsură au prețurile?](#ce-unitate-de-măsură-au-prețurile)
- [Prețurile includ TVA?](#prețurile-includ-tva)
- [Ce rezoluție ar trebui să folosesc?](#ce-rezoluție-ar-trebui-să-folosesc)
- [Ce fus orar folosește OPCOM?](#ce-fus-orar-folosește-opcom)
- [Ce e licența și de ce am nevoie de ea?](#licență)
- [Am introdus licența dar senzorii tot arată „Licență necesară". De ce?](#am-introdus-licența-dar-senzorii-tot-arată-licență-necesară)
- [Ce este o „fereastră de preț"?](#ce-este-o-fereastră-de-preț)
- [Ce înseamnă „non-suprapuse"?](#ce-înseamnă-non-suprapuse)
- [Care e diferența dintre modul fereastră și modul individual?](#care-e-diferența-dintre-modul-fereastră-și-modul-individual)
- [Cum funcționează pragurile de preț pe senzori?](#cum-funcționează-pragurile-de-preț-pe-senzori)
- [Ce face senzorul „Toate prețurile"?](#ce-face-senzorul-toate-prețurile)
- [Ce face senzorul percentilă?](#ce-face-senzorul-percentilă)
- [Care e diferența dintre modul individual și rolling?](#care-e-diferența-dintre-modul-individual-și-rolling)
- [Cum calculez câte ore acoperă setările mele?](#cum-calculez-câte-ore-acoperă-setările-mele)
- [De ce senzorii de cumpărare și vânzare sunt activi simultan?](#de-ce-senzorii-de-cumpărare-și-vânzare-sunt-activi-simultan)
- [Senzorii afișează „Necunoscut" (Unknown)](#senzorii-afișează-necunoscut)
- [Senzorii binari sunt mereu Oprit (Off)](#senzorii-binari-sunt-mereu-oprit)
- [Cum automatizez încărcarea bateriei?](#cum-automatizez-încărcarea-bateriei)
- [Cum actualizez integrarea?](#cum-actualizez-integrarea)

---

## Generale

### Ce este OPCOM?

[Înapoi la cuprins](#top)

OPCOM este operatorul pieței de energie electrică din România. Prin Piața pentru Ziua Următoare (PZU / DAM), se stabilesc prețurile la care se tranzacționează energia electrică pentru fiecare interval al zilei următoare. Această integrare importă aceste prețuri publice în Home Assistant.

---

### De ce aș folosi această integrare?

[Înapoi la cuprins](#top)

Dacă ai un sistem de stocare energie (baterie, Powerwall), vehicul electric, pompă de căldură, sau pur și simplu vrei să consumi energie când e ieftină. Integrarea îți spune automat: cât costă energia acum, care sunt cele mai ieftine/scumpe ore ale zilei, și dacă ar trebui să încarci sau să exporți acum.

---

## Date și prețuri

### Când se publică prețurile de mâine?

[Înapoi la cuprins](#top)

OPCOM publică prețurile PZU de regulă **între orele 13:00 și 15:00 CET** pentru ziua următoare. Ora exactă variază ușor de la zi la zi. Până la publicare, senzorii „mâine" afișează „Necunoscut" (Unknown) — asta e **comportament normal**, nu o eroare. OPCOM nu publică niciodată date pentru D+2 (poimâine).

---

### Ce unitate de măsură au prețurile?

[Înapoi la cuprins](#top)

**RON/MWh** (lei pe megawatt-oră). Pentru a converti în RON/kWh (util pentru consum casnic), împarte la 1000. De exemplu: 500 RON/MWh = 0.50 RON/kWh.

---

### Prețurile includ TVA?

[Înapoi la cuprins](#top)

**Nu.** Prețurile OPCOM sunt prețul energiei „en-gros" pe piața de tranzacționare. Factura ta finală include și: TVA, accize, tarif transport (Transelectrica), tarif distribuție (operatorul zonal), alte taxe. Prețul OPCOM reprezintă de obicei 30–50% din factura totală.

---

### Ce rezoluție ar trebui să folosesc?

[Înapoi la cuprins](#top)

| Scenariu | Rezoluție recomandată |
|----------|-----------------------|
| Doar monitoring pe dashboard | `60` |
| Automatizare baterie/EV simplă | `60` |
| Control precis baterie | `15` sau `30` |
| Toate cele de mai sus | `15,30,60` |

Cu cât rezoluția e mai mică, cu atât ai mai multă granularitate, dar și mai multe entități (21 per rezoluție).

---

### De ce PT15 are date de volum, dar PT30 și PT60 nu?

[Înapoi la cuprins](#top)

Vine de la OPCOM, nu de la integrare. Exportul CSV la rezoluție de 15 minute include coloane suplimentare (zonă de tranzacționare, volum tranzacționat total, volum cumpărare, volum vânzare). Exporturile de 30 și 60 minute conțin doar interval, preț și rezoluție.

---

### Ce fus orar folosește OPCOM?

[Înapoi la cuprins](#top)

**CET** (Central European Time = `Europe/Berlin`), **nu ora României** (EET = `Europe/Bucharest`). Diferența e mereu 1 oră. Integrarea face calculele intern în CET, dar orele afișate în atributele senzorilor sunt convertite automat în timezone-ul configurat în HA. Nu trebuie să setezi timezone-ul HA pe CET.

---

## Licență

### Ce e licența și de ce am nevoie de ea?

[Înapoi la cuprins](#top)

Integrarea folosește un sistem de licențiere server-side (v3.5) cu semnături Ed25519 și HMAC-SHA256. Fără o licență validă, integrarea afișează doar senzorul „Licență necesară" și nu creează senzori funcționali.

La prima instalare ai o **perioadă de evaluare (trial)** cu funcționalitate completă. Senzorul de licență arată câte zile mai ai de trial. După expirarea trial-ului, ai nevoie de o licență activă.

Licențe disponibile la: [hubinteligent.org/licenta/opcom](https://hubinteligent.org/licenta/opcom)

După achiziție, introdu cheia de licență din OptionsFlow:

1. **Settings** → **Devices & Services** → **OPCOM** → **Configure**
2. Selectează **Licență**
3. Completează câmpul „Cheie licență"
4. Salvează

---

### Am introdus licența dar senzorii tot arată „Licență necesară"

[Înapoi la cuprins](#top)

Câteva cauze posibile:

1. **Licența nu a fost validată** — verifică logurile pentru mesaje cu `LICENSE`
2. **Serverul de licențe nu este accesibil** — dacă HA nu are acces la internet, validarea eșuează
3. **Cheie greșită** — verifică că ai copiat cheia corect, fără spații suplimentare
4. **Restartare necesară** — în rare cazuri, un restart al HA poate rezolva problema

Activează debug logging ([DEBUG.md](DEBUG.md)) și caută mesaje legate de licență.

---

## Ferestre și algoritm

### Ce este o „fereastră de preț"?

[Înapoi la cuprins](#top)

O fereastră este o perioadă continuă de timp (de exemplu, 1 oră) pentru care se calculează prețul mediu al energiei. Integrarea caută cele mai ieftine (sau scumpe) astfel de ferestre din zi.

---

### Ce înseamnă „non-suprapuse"?

[Înapoi la cuprins](#top)

Algoritmul greedy garantează că nicio fereastră selectată nu partajează intervale cu alta. Fără această garanție, „top 6 ferestre" ar fi practic același bloc de ore cu mici variații.

---

### Care e diferența dintre modul fereastră și modul individual?

[Înapoi la cuprins](#top)

**Mod fereastră** („Ar trebui să încarce/exporte acum") — selectează blocuri consecutive de intervale și calculează media prețului pe bloc. Suportă prag de preț opțional. Util când ai nevoie de perioade neîntrerupte (ciclu complet de încărcare baterie, programare EV).

**Mod individual** („Interval ieftin/scump acum") — selectează fiecare interval pe baza prețului propriu, fără a ține cont de vecini. Util când vrei să maximizezi valoarea per interval (export grid la prețul maxim).

Acoperirea totală (număr de intervale selectate) e identică — doar distribuția diferă.

---

### Cum funcționează pragurile de preț pe senzori?

[Înapoi la cuprins](#top)

Pragurile configurate (`price_threshold_low` și `price_threshold_high`) au efect pe două tipuri de senzori:

**Senzori de fereastră** (`Ar trebui să încarce/exporte acum`): Senzorul se activează doar dacă intervalul curent e într-o fereastră optimă **ȘI** prețul trece pragul. Exemplu: ai `price_threshold_high = 600`. Chiar dacă intervalul curent e în top 6 cele mai scumpe ferestre, dacă prețul e doar 550 RON/MWh, senzorul de export rămâne OFF. Dacă pragul nu e configurat (câmpul e gol), senzorul funcționează doar pe baza ferestrei.

**Senzori de prag** (`Preț sub prag` / `Preț peste prag`): Senzori simpli — doar preț curent vs prag. ON dacă prețul trece pragul, indiferent de ferestre. Dacă pragul nu e configurat, acești senzori rămân permanent OFF.

Atributele senzorilor de fereastră afișează prețul curent, pragul configurat, și motivul blocării (dacă senzorul e OFF din cauza pragului).

---

### Ce face senzorul „Toate prețurile"?

[Înapoi la cuprins](#top)

Senzorul `Toate prețurile azi` / `Toate prețurile mâine` oferă în atribute un dict complet `HH:MM → preț` cu toate intervalele zilei. Valoarea principală (state) e media aritmetică a zilei.

Util pentru Apexcharts (grafice de preț pe zi), Node-RED, template sensors, sau orice automatizare care are nevoie de toate prețurile dintr-un singur senzor. Senzorul de mâine arată Unknown până la publicarea prețurilor (~13:00–15:00 CET).

---

### Ce face senzorul percentilă?

[Înapoi la cuprins](#top)

Senzorul `Percentilă preț acum` returnează un număr între 0 și 100 care indică unde se situează prețul curent în distribuția zilei: 0% = cel mai ieftin interval, 100% = cel mai scump, 50% = median.

Senzorul binar `Preț ieftin azi (bottom 25%)` e ON când percentila e sub 25%. `Preț scump azi (top 25%)` e ON când percentila e peste 75%. Pragul de 25% e fix (nu configurabil).

Senzorul numeric e `state_class: measurement`, deci HA înregistrează statistici long-term (min, max, mean).

---

### Care e diferența dintre modul individual și rolling?

[Înapoi la cuprins](#top)

**Mod individual** (`Interval ieftin/scump acum`) selectează din **toată ziua** (inclusiv intervale trecute). Lista e stabilă — nu se schimbă pe parcursul zilei.

**Mod rolling** (`Ieftin/Scump din intervalele rămase`) selectează doar din ce a mai **rămas din zi** (de acum încolo). Lista se recalculează la fiecare interval.

Când folosești fiecare: modul individual e mai previzibil (ferestrele sunt fixe de dimineață). Modul rolling e mai adaptiv (dacă cele mai ieftine ore au trecut, recalculează pe ce a mai rămas).

---

### Cum calculez câte ore acoperă setările mele?

[Înapoi la cuprins](#top)

Formula: `top_n_windows × window_minutes ÷ 60 = ore acoperite`

Exemplu: `top_n_windows = 6`, `window_minutes = 60` → 6 × 60 ÷ 60 = **6 ore** de cumpărare + **6 ore** de vânzare.

Dacă ai configurat `top_n_per_resolution`, fiecare rezoluție va folosi propriul top N. Detalii în [SETUP.md](SETUP.md#cum-calculezi-corect-relația-dintre-fereastră-și-număr-ferestre).

---

### De ce senzorii de cumpărare și vânzare sunt activi simultan?

[Înapoi la cuprins](#top)

Cel mai probabil `top_n_windows` e prea mare. Cu `top_n_windows = 16` și `window_minutes = 60`: 16 ore „ieftine" + 16 ore „scumpe" = 32, dar ziua are 24 → minim 8 ore se suprapun. Redu `top_n_windows` astfel încât orele acoperite să nu depășească 8–10 ore.

---

## Automatizări

### Cum automatizez încărcarea bateriei?

[Înapoi la cuprins](#top)

Cel mai simplu: folosește senzorul binar ca trigger.

```yaml
automation:
  - alias: "Încarcă bateria"
    trigger:
      platform: state
      entity_id: binary_sensor.ar_trebui_sa_incarce_acum_pt60
      to: "on"
    action:
      service: switch.turn_on
      entity_id: switch.battery_charger
```

Cu pragul configurat (ex: `price_threshold_low = 400`), senzorul se activează doar dacă e într-o fereastră ieftină ȘI prețul ≤ 400 RON/MWh.

### Pot folosi template sensors pentru conversii?

[Înapoi la cuprins](#top)

Da. De exemplu, pentru a avea prețul în RON/kWh:

```yaml
template:
  - sensor:
      - name: "Preț energie acum (RON/kWh)"
        unit_of_measurement: "RON/kWh"
        state: "{{ states('sensor.pret_acum_pt60_azi') | float / 1000 }}"
```

---

## Troubleshooting

### Senzorii afișează „Necunoscut"

[Înapoi la cuprins](#top)

Cauze posibile: datele de mâine nu sunt publicate încă (normal până la ~14:00 CET), eroare la descărcare (verifică logurile), lipsă conexiune la internet, OPCOM.ro temporar indisponibil, sau licență invalidă/expirată. Dacă TOATE senzorii arată Unknown, verifică mai întâi licența.

---

### Senzorii binari sunt mereu Oprit

[Înapoi la cuprins](#top)

Cauze posibile: intervalul curent nu e într-o fereastră optimă (mărește `top_n_windows`), pragul de preț blochează activarea (verifică atributele — dacă vezi „Blocat de prag", ajustează pragul), sau licență invalidă (senzorii binari returnează `None` fără licență).

---

## Actualizări

### Cum actualizez integrarea?

[Înapoi la cuprins](#top)

**HACS**: HACS te notifică automat când e o versiune nouă. Click **Update**.

**Manual**: descarcă noua versiune, suprascrie fișierele din `custom_components/opcom/`, restartează HA.

Setările NU se pierd la actualizare — sunt stocate în baza de date HA, nu în fișiere.

---

## Performanță

### Câte request-uri face integrarea?

[Înapoi la cuprins](#top)

La fiecare ciclu de actualizare (implicit 15 minute): 3 rezoluții × 2 zile = 6 request-uri HTTP. Fiecare request descarcă un CSV mic (~5–15 KB). Total: ~90 KB la fiecare 15 minute = ~8.6 MB/zi. Integrarea are retry automat cu backoff exponențial (3 tentative) la erori de rețea.

### Pot reduce traficul de rețea?

[Înapoi la cuprins](#top)

Da: mărește `scan_interval_minutes` (ex: 30 sau 60), redu numărul de rezoluții (ex: doar `60`), sau setează `days_ahead` la `1` (nu descarcă mâine).
