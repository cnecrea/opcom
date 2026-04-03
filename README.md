# OPCOM România — Integrare Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1.0%2B-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/cnecrea/opcom)](https://github.com/cnecrea/opcom/releases)
[![GitHub Stars](https://img.shields.io/github/stars/cnecrea/opcom?style=flat&logo=github)](https://github.com/cnecrea/opcom/stargazers)
[![Instalări](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/cnecrea/opcom/main/statistici/shields/descarcari.json)](https://github.com/cnecrea/opcom)
[![Ultima versiune](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/cnecrea/opcom/main/statistici/shields/ultima_release.json)](https://github.com/cnecrea/opcom/releases/latest)

Integrare custom pentru [Home Assistant](https://www.home-assistant.io/) care importă **prețurile energiei electrice** de pe [OPCOM România](https://www.opcom.ro/) — Piața pentru Ziua Următoare (PZU/DAM).

Oferă senzori în timp real pentru prețul curent, ferestre optime de cumpărare/vânzare, și semnale binare pentru automatizarea încărcării/descărcării bateriilor (sisteme de stocare energie, vehicule electrice, pompe de căldură etc.).

---

## Licență

Integrarea folosește un **sistem de licențiere server-side** (v3.5) cu semnături Ed25519 și HMAC-SHA256. Fără o licență validă, integrarea afișează doar senzorul **„Licență necesară"** și nu creează senzori funcționali.

La prima instalare ai o **perioadă de evaluare (trial)** cu funcționalitate completă. După expirarea trial-ului, ai nevoie de o licență activă.

Licențe disponibile la: [hubinteligent.org/donate?ref=opcom](https://hubinteligent.org/donate?ref=opcom)

Cheia de licență se introduce din **OptionsFlow**:

1. **Settings** → **Devices & Services** → **OPCOM** → **Configure**
2. Selectează **Licență**
3. Completează câmpul „Cheie licență"
4. Salvează

Detalii complete despre tipurile de licență, trial, și troubleshooting în [FAQ.md](FAQ.md#licență).

---

## Ce face integrarea

- **Descarcă automat** prețurile PZU de pe OPCOM.ro la interval configurabil (implicit: 15 minute)
- **Suportă 3 rezoluții**: PT15M (96 intervale/zi), PT30M (48 intervale/zi), PT60M (24 intervale/zi)
- **Calculează ferestrele optime** de preț ieftin/scump — **non-suprapuse** (algoritm greedy)
- **5 moduri de selecție binară**: fereastră (cu prag opțional), individual, prag de preț, percentilă, rolling
- **Prag de preț pe ferestre**: senzorii „Ar trebui să încarce/exporte" respectă pragurile configurate — nu activează dacă prețul nu trece pragul, chiar dacă intervalul e într-o fereastră optimă
- **Senzori „Toate prețurile"**: dict complet HH:MM → preț pentru azi și mâine, util pentru Apexcharts, Node-RED, template sensors
- **Percentilă zilnică**: senzor numeric 0–100% care indică poziția prețului curent în distribuția zilei
- **Top N per rezoluție** (opțional): poți configura un număr diferit de ferestre pentru fiecare rezoluție
- **Intervale rămase**: câte sloturi „bune" mai rămân azi (pentru planificarea bateriei)
- **Izolare la eșec**: o zi sau rezoluție eșuată nu afectează celelalte — senzorii afectați arată Unknown, restul funcționează normal
- **Retry automat**: backoff exponențial (3 tentative) la erori de rețea

---

## Sursa datelor

Datele vin direct de pe OPCOM.ro, fișiere CSV publice:

```
https://www.opcom.ro/rapoarte-pzu-raportPIP-export-csv/{zi}/{luna}/{an}/{lang}?resolution={res}
```

OPCOM publică prețurile PZU (Piața pentru Ziua Următoare) **în fiecare zi**, de regulă **între orele 13:00 și 15:00 CET**, pentru ziua următoare. Până la publicare, senzorii „mâine" afișează „Necunoscut" (Unknown) — comportament normal.

---

## Referință de timp: CET, nu ora României

**OPCOM operează pe CET** (Central European Time = `Europe/Berlin`), **nu pe ora României** (EET = `Europe/Bucharest`). Asta e confirmat oficial de OPCOM:

> _„Toate aspectele legate de participarea pe PZU se raportează la ore CET."_

**Ce înseamnă concret:**
- Intervalul 1 din CSV = **00:00–00:15 CET** = **01:00–01:15 ora României**
- Ultimul interval al zilei se termină la **00:00 CET** = **01:00 ora României** (ziua următoare)
- Diferența e **mereu 1 oră** (iarna CET→EET = +1h, vara CEST→EEST = +1h — se schimbă simultan)

**Integrarea gestionează asta automat pe două niveluri:**
- **Calculele interne** (intervalul curent, ziua de livrare, ferestrele optime) sunt făcute în CET — asigură sincronizarea exactă cu datele OPCOM
- **Orele afișate în atributele senzorilor** (ex: `Ora: "22:45 → 23:45"`) sunt convertite automat în timezone-ul configurat în Home Assistant — nu trebuie să faci nicio conversie manuală

Nu trebuie să schimbi nimic în setările HA.

---

## Instalare

### HACS (recomandat)

1. Deschide HACS în Home Assistant
2. Click pe cele 3 puncte (⋮) din colțul dreapta sus → **Custom repositories**
3. Adaugă URL-ul: `https://github.com/cnecrea/opcom`
4. Categorie: **Integration**
5. Click **Add** → găsește „OPCOM România" → **Install**
6. Restartează Home Assistant

### Manual

1. Copiază folderul `custom_components/opcom/` în directorul `config/custom_components/` din Home Assistant
2. Restartează Home Assistant

---

## Configurare

### Pasul 1 — Adaugă integrarea

1. **Settings** → **Devices & Services** → **Add Integration**
2. Caută „**OPCOM**" sau „**OPCOM România**"
3. Completează formularul:

| Câmp | Descriere | Implicit | Interval permis |
|------|-----------|----------|-----------------|
| **Limbă** | Limba exportului CSV (`ro` sau `en`) | `ro` | `ro`, `en` |
| **Rezoluții** | Rezoluțiile în minute, separate prin virgulă | `15,30,60` | Oricare din: `15`, `30`, `60` |
| **Zile în avans** | Câte zile să descarce (1 = doar azi, 2 = azi + mâine) | `2` | 1–2 |
| **Interval actualizare** | La câte minute să reîmprospăteze datele | `15` | 5–180 minute |
| **Fereastră planificare** | Durata ferestrei de preț optim (în minute) | `60` | minim 15 minute |
| **Număr ferestre** | Câte ferestre optime să calculeze (top N global) | `6` | 1–24 |
| **Ferestre per rezoluție** | (opțional) Override top N per rezoluție, format: `15:4,30:6,60:2` | gol | rezoluție:număr |
| **Prag preț mic** | (opțional) Pragul maxim de import — senzorul de încărcare se activează doar sub acest preț | gol | -500 – 10000 RON/MWh |
| **Prag preț mare** | (opțional) Pragul minim de export — senzorul de export se activează doar peste acest preț | gol | -500 – 10000 RON/MWh |

### Pasul 2 — Licență

După configurarea inițială, introdu licența din **OptionsFlow**:

1. **Settings** → **Devices & Services** → **OPCOM** → **Configure**
2. Selectează **Licență**
3. Introdu cheia de licență
4. Salvează

### Pasul 3 — Reconfigurare (opțional)

Toate setările pot fi modificate după instalare, fără a șterge integrarea:

1. **Settings** → **Devices & Services** → click pe integrarea **OPCOM**
2. Click pe **Configure** (⚙️) → selectează **Setări**
3. Modifică setările dorite → **Submit**
4. Integrarea se reîncarcă automat cu noile setări

---

## Entități create

Integrarea creează un **device** numit „OPCOM România". Sub el, pentru **fiecare rezoluție** configurată, se creează **21 entități** (11 senzori + 10 senzori binari).

Cu configurarea implicită (3 rezoluții: 15, 30, 60) = **63 entități** total.

Fără licență validă, se creează doar senzorul **„Licență necesară"** (`sensor.opcom_licenta_{entry_id}`).

### Senzori de preț (2 per rezoluție)

| Entitate | Entity ID | Descriere | Unitate |
|----------|-----------|-----------|---------|
| `[15] Preț acum` | `sensor.pret_acum_pt15_azi` | Prețul intervalului curent | RON/MWh |
| `[15] Preț următor` | `sensor.pret_urmator_pt15_azi` | Prețul intervalului următor (sau primul de mâine la ultimul interval) | RON/MWh |

**Atribute `Preț acum`:**

```
Data: 2026-04-03
Rezolutie: PT15M
Interval: 45
Ora: 12:00 → 12:15
Zona: Romania
Informatii actualizare:
Actualizare reusita: true
Versiune date: 12
```

### Senzori ferestre optime (4 per rezoluție: ieftin/scump × azi/mâine)

| Entitate | Entity ID | Descriere |
|----------|-----------|-----------|
| `[15] Cea mai ieftină fereastră azi` | `sensor.cea_mai_ieftina_fereastra_azi_pt15_azi` | Prețul mediu al celei mai ieftine ferestre azi |
| `[15] Cea mai scumpă fereastră azi` | `sensor.cea_mai_scumpa_fereastra_azi_pt15_azi` | Prețul mediu al celei mai scumpe ferestre azi |
| `[15] Cea mai ieftină fereastră mâine` | `sensor.cea_mai_ieftina_fereastra_maine_pt15_maine` | La fel, pentru mâine (Unknown până la ~14:00 CET) |
| `[15] Cea mai scumpă fereastră mâine` | `sensor.cea_mai_scumpa_fereastra_maine_pt15_maine` | La fel, pentru mâine |

**Valoare principală (state):** prețul mediu al celei mai bune ferestre (RON/MWh).

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Durata fereastra: 60 min
Nr. ferestre: 6
Lista ferestre de pret:
Fereastra 1: 03:00 → 04:00 · medie 295.43 RON/MWh · int. 13–16
Fereastra 2: 00:00 → 01:00 · medie 310.22 RON/MWh · int. 1–4
Fereastra 3: 14:00 → 15:00 · medie 318.50 RON/MWh · int. 57–60
```

Ferestrele sunt **sortate cronologic** (după ora de început) și sunt **non-suprapuse**.

Dacă fereastra are un singur interval (window_minutes = rezoluție), textul afișează prețul direct, nu „medie":

```
Fereastra 1: 03:15 → 03:30 · 295.43 RON/MWh · int. 14
```

### Senzori intervale rămase (2 per rezoluție)

| Entitate | Entity ID | Descriere |
|----------|-----------|-----------|
| `[15] Intervale rămase cumpărare azi` | `sensor.intervale_ramase_cumparare_pt15_azi` | Câte intervale ieftine mai rămân de acum încolo |
| `[15] Intervale rămase vânzare azi` | `sensor.intervale_ramase_vanzare_pt15_azi` | Câte intervale scumpe mai rămân de acum încolo |

Valoarea scade natural spre 0 pe parcursul zilei. Ferestrele sunt recalculate doar din intervale viitoare.

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Durata fereastra: 60 min
Nr. ferestre: 6
Interval curent: 45
Intervale ramase: 8
Lista intervale ramase:
Pozitia 5: int. 73 · 18:00 → 18:15 · 580.20 RON/MWh
Pozitia 6: int. 74 · 18:15 → 18:30 · 575.10 RON/MWh
```

Intervalele rămase sunt **sortate cronologic**. `Pozitia X` indică **rangul intervalului în topul de preț** (Pozitia 1 = cel mai bun preț din top), nu ordinea din listă.

### Senzor percentilă (1 per rezoluție)

| Entitate | Entity ID | Descriere | Unitate |
|----------|-----------|-----------|---------|
| `[15] Percentilă preț acum` | `sensor.percentila_pret_pt15_azi` | Poziția prețului curent în distribuția zilei | % |

Valoare: 0% = cel mai ieftin interval din zi, 100% = cel mai scump. 50% = median.

Util în automatizări: „dacă percentila < 30%, încarcă bateria".

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Interval curent: 45
Pret curent: 322.56 RON/MWh
Percentila: 35.8%
```

### Senzori „Toate prețurile" (2 per rezoluție: azi + mâine)

| Entitate | Entity ID | Descriere | Unitate |
|----------|-----------|-----------|---------|
| `[15] Toate prețurile azi` | `sensor.toate_preturile_azi_pt15_azi` | Media zilei + dict complet HH:MM → preț | RON/MWh |
| `[15] Toate prețurile mâine` | `sensor.toate_preturile_maine_pt15_maine` | La fel, pentru mâine | RON/MWh |

**Valoare principală (state):** media aritmetică a tuturor prețurilor zilei (RON/MWh).

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Nr. intervale: 96
Preturi:
01:00: 295.43
01:15: 287.61
01:30: 301.22
...
00:45: 412.50
```

Orele sunt în timezone-ul local HA (convertite din CET). Sortare cronologică.

Util pentru **Apexcharts**, **Node-RED**, **template sensors**, sau orice automatizare care are nevoie de toate prețurile zilei într-un singur senzor.

### Senzori binari — mod fereastră cu prag (2 per rezoluție)

| Entitate | Entity ID | Pornit când... |
|----------|-----------|----------------|
| `[15] Ar trebui să încarce acum` | `binary_sensor.ar_trebui_sa_incarce_acum_pt15` | Intervalul curent e într-o fereastră ieftină **ȘI** prețul ≤ pragul de import (dacă e configurat) |
| `[15] Ar trebui să exporte acum` | `binary_sensor.ar_trebui_sa_exporte_acum_pt15` | Intervalul curent e într-o fereastră scumpă **ȘI** prețul ≥ pragul de export (dacă e configurat) |

**Comportament praguri:** Dacă ai configurat `price_threshold_low` (prag preț mic), senzorul de încărcare se activează doar dacă e într-o fereastră ieftină **ȘI** prețul curent ≤ prag. Dacă prețul e peste prag, senzorul rămâne OFF chiar dacă intervalul e într-o fereastră optimă. La fel, `price_threshold_high` (prag preț mare) filtrează senzorul de export. Dacă pragurile nu sunt configurate, senzorii funcționează ca înainte (doar pe baza ferestrei).

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Mod: fereastra
Durata fereastra: 60 min
Nr. ferestre: 6
Interval curent: 45
Pret curent: 322.56 RON/MWh
Prag: 400.00 RON/MWh
Fereastra activa: 11:00 → 12:00 · medie 322.56 RON/MWh · int. 45–48
```

Dacă senzorul e OFF din cauza pragului:

```
Fereastra activa: 11:00 → 12:00 · medie 322.56 RON/MWh · int. 45–48
Blocat de prag: preț sub prag export
```

### Senzori binari — mod individual (2 per rezoluție)

| Entitate | Entity ID | Pornit când... |
|----------|-----------|----------------|
| `[15] Interval ieftin acum` | `binary_sensor.interval_ieftin_acum_pt15` | Prețul propriu al intervalului curent e în top N |
| `[15] Interval scump acum` | `binary_sensor.interval_scump_acum_pt15` | Prețul propriu al intervalului curent e în top N |

Selectează fiecare interval pe baza prețului propriu, fără a ține cont de vecini. Acoperire identică cu modul fereastră (același număr de intervale), dar distribuție diferită.

**Atribute:**

```
Data: 2026-04-03
Rezolutie: PT15M
Mod: individual
Intervale selectate: 16
Interval curent: 45
Interval activ: int. 45 · 12:00 → 12:15 · 580.20 RON/MWh
Top intervale din toata ziua:
Pozitia 3: int. 73 · 18:00 → 18:15 · 719.00 RON/MWh
Pozitia 2: int. 74 · 18:15 → 18:30 · 723.00 RON/MWh
```

Intervalele sunt **sortate cronologic**. `Pozitia X` indică **rangul intervalului în topul de preț** (Pozitia 1 = cel mai bun preț din top), nu ordinea din listă.

### Senzori binari — prag de preț (2 per rezoluție)

| Entitate | Entity ID | Pornit când... |
|----------|-----------|----------------|
| `[15] Preț sub prag acum` | `binary_sensor.pret_sub_prag_pt15` | Prețul curent ≤ pragul configurat (price_threshold_low) |
| `[15] Preț peste prag acum` | `binary_sensor.pret_peste_prag_pt15` | Prețul curent ≥ pragul configurat (price_threshold_high) |

Senzori simpli — doar preț curent vs prag. Fără calcule de fereastră. Dacă pragul nu e configurat, senzorul rămâne OFF permanent.

### Senzori binari — percentilă (2 per rezoluție)

| Entitate | Entity ID | Pornit când... |
|----------|-----------|----------------|
| `[15] Preț ieftin azi (bottom 25%)` | `binary_sensor.pret_ieftin_percentila_pt15` | Prețul curent e în bottom 25% al zilei |
| `[15] Preț scump azi (top 25%)` | `binary_sensor.pret_scump_percentila_pt15` | Prețul curent e în top 25% al zilei |

### Senzori binari — rolling window (2 per rezoluție)

| Entitate | Entity ID | Pornit când... |
|----------|-----------|----------------|
| `[15] Ieftin din intervalele rămase` | `binary_sensor.ieftin_din_ramase_pt15` | Intervalul curent e printre cele mai ieftine din ce a mai rămas din zi |
| `[15] Scump din intervalele rămase` | `binary_sensor.scump_din_ramase_pt15` | Intervalul curent e printre cele mai scumpe din ce a mai rămas din zi |

Diferența față de modul individual: acela selectează din toată ziua (inclusiv trecut), rolling selectează doar din intervalele viitoare. Util: „acum e un moment bun comparativ cu ce a mai rămas", nu „comparativ cu toată ziua".

### Senzor licență (fără licență validă)

| Entitate | Entity ID | Valoare | Icon |
|----------|-----------|---------|------|
| Licență necesară | `sensor.opcom_licenta_{entry_id}` | „Licență necesară" / „Trial — X zile rămase" / „Licență expirată" | mdi:license |

Acest senzor apare **doar** când licența nu este validă. Când licența e activă, senzorul dispare și sunt creați toți senzorii normali.

---

## Prefixul de rezoluție

Toate entitățile au un prefix care indică rezoluția: `[15]`, `[30]`, sau `[60]`.

- `[15]` = PT15M — intervale de 15 minute (96 pe zi). Cel mai granular.
- `[30]` = PT30M — intervale de 30 minute (48 pe zi).
- `[60]` = PT60M — intervale de 60 minute (24 pe zi). Cel mai comun.

Poți configura una, două, sau toate trei rezoluțiile. Dacă nu ai nevoie de granularitate, folosește doar `60`.

---

## Algoritmul de ferestre

### Cum funcționează

1. **Sliding window**: pentru fiecare poziție posibilă în ziua respectivă, se calculează media prețurilor pe durata ferestrei (ex: 4 intervale × 15 min = 60 min)
2. **Sortare**: toate candidatele sunt sortate crescător (pentru ieftin) sau descrescător (pentru scump)
3. **Selecție greedy non-suprapusă**: se alege cea mai bună fereastră, se marchează intervalele ca ocupate, apoi se alege următoarea cea mai bună care NU se suprapune cu cele deja selectate. Se repetă până se ating top N ferestre.

### Algoritmul individual

Modul individual e mai simplu:

1. **Sortare directă**: toate intervalele zilei sunt sortate după prețul propriu
2. **Selecție top N**: se aleg primele N intervale, unde N = `top_n_windows × (window_minutes ÷ res_minutes)`
3. **Verificare**: senzorul e ON dacă intervalul curent e în lista selectată

### Diferența fereastră vs individual

| Aspect | Mod fereastră | Mod individual |
|--------|:---:|:---:|
| Selecție | Blocuri consecutive, medie | Fiecare interval pe merit propriu |
| Continuitate | Garantată | Nu e garantată |
| Util pentru | Încărcare baterie, EV | Export grid la preț maxim |
| Prag de preț | Da (opțional) | Nu |

### Cum calculezi setările corect

Formula: `top_n_windows × window_minutes ÷ 60 = ore acoperite`

**⚠️ Atenție:** dacă `top_n_windows` e prea mare, senzorii de cumpărare ȘI vânzare se activează simultan. Regulă practică: nu depăși 8–10 ore acoperite. Detalii și tabel de referință în [SETUP.md](SETUP.md#cum-calculezi-corect-relația-dintre-fereastră-și-număr-ferestre).

---

## Exemple de automatizări

### Încarcă bateria când prețul e mic (mod fereastră cu prag)

```yaml
automation:
  - alias: "Încarcă bateria la preț ieftin"
    trigger:
      - platform: state
        entity_id: binary_sensor.ar_trebui_sa_incarce_acum_pt15
        to: "on"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.battery_charger

  - alias: "Oprește încărcarea"
    trigger:
      - platform: state
        entity_id: binary_sensor.ar_trebui_sa_incarce_acum_pt15
        to: "off"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.battery_charger
```

Cu pragul configurat (ex: `price_threshold_low = 400`), senzorul se activează doar dacă e într-o fereastră ieftină ȘI prețul curent ≤ 400 RON/MWh.

### Exportă energie când prețul e mare (mod individual)

```yaml
automation:
  - alias: "Exportă la preț scump (per interval)"
    trigger:
      - platform: state
        entity_id: binary_sensor.interval_scump_acum_pt15
        to: "on"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.grid_export

  - alias: "Oprește exportul"
    trigger:
      - platform: state
        entity_id: binary_sensor.interval_scump_acum_pt15
        to: "off"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.grid_export
```

### Automatizare bazată pe percentilă

```yaml
automation:
  - alias: "Încarcă bateria dacă prețul e în bottom 30%"
    trigger:
      - platform: numeric_state
        entity_id: sensor.percentila_pret_pt60_azi
        below: 30
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.battery_charger
```

### Notificare când se publică prețurile de mâine

```yaml
automation:
  - alias: "Prețuri mâine disponibile"
    trigger:
      - platform: state
        entity_id: sensor.toate_preturile_maine_pt60_maine
        from: "unknown"
    action:
      - service: notify.mobile_app
        data:
          title: "OPCOM: Prețuri mâine"
          message: >
            Media zilei de mâine: {{ states('sensor.toate_preturile_maine_pt60_maine') }} RON/MWh
```

---

## Diagnostics

Integrarea expune date complete de diagnosticare prin mecanismul standard HA:

1. **Settings** → **Devices & Services** → click pe **OPCOM**
2. Click pe cele 3 puncte (⋮) → **Download diagnostics**

Fișierul JSON conține configurația completă, lista entităților, toate datele raw descărcate, și starea ultimei actualizări. Nu conține date personale sau credențiale.

---

## Structura fișierelor

```
custom_components/opcom/
├── __init__.py          # Setup/unload integrare (licență, heartbeat, lifecycle)
├── api.py               # Descărcare CSV + parsing OPCOM
├── binary_sensor.py     # 5 tipuri senzori binari (fereastră, individual, prag, percentilă, rolling)
├── config_flow.py       # ConfigFlow + OptionsFlow (setări + licență, meniu)
├── const.py             # Constante, defaults, OpcomSettings dataclass
├── coordinator.py       # DataUpdateCoordinator — fetch centralizat cu retry
├── diagnostics.py       # Export diagnostics
├── helpers.py           # Funcții comune (ferestre, individual, formatare, percentilă)
├── license.py           # Manager licență (server-side v3.5, Ed25519, HMAC-SHA256)
├── manifest.json        # Metadata integrare
├── sensor.py            # 6 tipuri senzori + LicentaNecesaraSensor
├── strings.json         # Traduceri implicite
└── translations/
    ├── en.json          # Traduceri engleză
    └── ro.json          # Traduceri română
```

---

## Cerințe

- **Home Assistant** 2024.1.0 sau mai nou
- **Licență** validă — de la [hubinteligent.org/donate?ref=opcom](https://hubinteligent.org/donate?ref=opcom) (trial disponibil)
- **HACS** (opțional, pentru instalare ușoară)
- **Acces la internet** — integrarea descarcă date de pe opcom.ro și validează licența

Nu necesită dependențe externe Python suplimentare.

---

## Limitări cunoscute

1. **Prețurile de mâine nu sunt disponibile imediat** — OPCOM le publică de obicei între 13:00–15:00 CET. Până atunci, senzorii „mâine" afișează „Necunoscut".

2. **O singură instanță** — integrarea suportă o singură configurare. Dacă încerci să adaugi a doua, vei primi eroare „already configured".

3. **Rezoluția PT15M include date suplimentare** — exportul CSV la 15 minute conține și coloane de volum tranzacționat + zonă. Rezoluțiile de 30 și 60 minute au doar preț. Asta vine de la OPCOM, nu de la integrare.

4. **Senzorii binari folosesc doar ziua curentă** — deciziile de ON/OFF se bazează exclusiv pe datele zilei curente. Senzorii de ferestre „mâine" afișează datele de mâine ca referință, dar senzorii binari nu combină azi cu mâine.

5. **Zile în avans: maxim 2** — OPCOM nu publică niciodată date pentru D+2 (poimâine). Valoarea maximă este 2 (azi + mâine).

---

## ☕ Susține dezvoltatorul

Dacă ți-a plăcut această integrare și vrei să sprijini munca depusă, **invită-mă la o cafea**! 🫶
Nu costă nimic, iar contribuția ta ajută la dezvoltarea viitoare a proiectului. 🙌

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Susține%20dezvoltatorul-orange?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/cnecrea)

Mulțumesc pentru sprijin și apreciez fiecare gest de susținere! 🤗

---

## 🧑‍💻 Contribuții

Contribuțiile sunt binevenite! Simte-te liber să trimiți un pull request sau să raportezi probleme [aici](https://github.com/cnecrea/opcom/issues).

---

## 🌟 Suport
Dacă îți place această integrare, oferă-i un ⭐ pe [GitHub](https://github.com/cnecrea/opcom/)! 😊
