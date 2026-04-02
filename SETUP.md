# Ghid de instalare și configurare — OPCOM România

Acest ghid acoperă fiecare pas al instalării și configurării integrării OPCOM România pentru Home Assistant. Dacă ceva nu e clar, deschide un [issue pe GitHub](https://github.com/cnecrea/opcom/issues).

---

## Cerințe preliminare

Înainte de a începe, asigură-te că ai:

- **Home Assistant** versiunea 2024.1.0 sau mai nouă
- **Licență** validă — de la [hubinteligent.org/licenta/opcom](https://hubinteligent.org/licenta/opcom) (trial disponibil la prima instalare)
- **Acces la internet** din instanța HA (integrarea descarcă date de pe opcom.ro și validează licența)
- **HACS** instalat (opțional, dar recomandat) — [instrucțiuni HACS](https://hacs.xyz/docs/setup/download)

### Notă importantă: fusul orar CET

OPCOM operează pe **CET** (Central European Time = `Europe/Berlin`), **nu pe ora României** (EET = `Europe/Bucharest`). Intervalul 1 din exportul OPCOM = 00:00 CET = 01:00 ora României.

**Nu trebuie să schimbi nimic.** Integrarea face automat toate calculele în CET, iar orele afișate în atributele senzorilor sunt convertite automat în timezone-ul configurat în HA.

---

## Metoda 1: Instalare prin HACS (recomandat)

### Pasul 1 — Adaugă repository-ul custom

1. Deschide Home Assistant → sidebar → **HACS**
2. Click pe cele 3 puncte (⋮) din colțul dreapta sus
3. Selectează **Custom repositories**
4. În câmpul „Repository" scrie: `https://github.com/cnecrea/opcom`
5. În câmpul „Category" selectează: **Integration**
6. Click **Add**

### Pasul 2 — Instalează integrarea

1. În HACS, caută „**OPCOM**" sau „**OPCOM România**"
2. Click pe rezultat → **Download** (sau **Install**)
3. Confirmă instalarea

### Pasul 3 — Restartează Home Assistant

1. **Settings** → **System** → **Restart**
2. Sau din terminal: `ha core restart`

**Așteptare**: restartul durează 1–3 minute. Nu continua până nu se încarcă complet dashboard-ul.

---

## Metoda 2: Instalare manuală

### Pasul 1 — Descarcă fișierele

1. Mergi la [Releases](https://github.com/cnecrea/opcom/releases) pe GitHub
2. Descarcă ultima versiune (zip sau tar.gz)
3. Dezarhivează

### Pasul 2 — Copiază folderul

Copiază întregul folder `custom_components/opcom/` în directorul de configurare al Home Assistant:

```
config/
└── custom_components/
    └── opcom/
        ├── __init__.py
        ├── api.py
        ├── binary_sensor.py
        ├── config_flow.py
        ├── const.py
        ├── coordinator.py
        ├── diagnostics.py
        ├── helpers.py
        ├── license.py
        ├── manifest.json
        ├── sensor.py
        ├── strings.json
        └── translations/
            ├── en.json
            └── ro.json
```

**Atenție**: folderul trebuie să fie exact `opcom` (litere mici, fără spații).

### Pasul 3 — Restartează Home Assistant

La fel ca la Metoda 1.

---

## Configurare inițială

### Pasul 1 — Adaugă integrarea

1. **Settings** → **Devices & Services**
2. Click **+ Add Integration** (butonul albastru, dreapta jos)
3. Caută „**OPCOM**" — va apărea „OPCOM Romania" (sau „OPCOM România")
4. Click pe ea

### Pasul 2 — Completează formularul

Vei vedea un formular cu 9 câmpuri. Le poți lăsa pe toate pe valorile implicite dacă nu ai nevoi speciale.

#### Câmp 1: Limbă (`lang`)

- **Ce face**: setează limba exportului CSV de pe OPCOM
- **Valori**: `ro` sau `en`
- **Implicit**: `ro`
- **Când schimbi**: dacă vrei header-ele CSV în engleză (afectează doar logurile, nu numele senzorilor)

#### Câmp 2: Rezoluții (`resolutions`)

- **Ce face**: alege granularitatea intervalelor de preț
- **Format**: numere separate prin virgulă: `15,30,60`
- **Valori permise**: `15`, `30`, `60` (oricare combinație)
- **Implicit**: `15,30,60` (toate trei)
- **Impact**: fiecare rezoluție creează 21 entități (11 senzori + 10 binari). Cu toate 3 = 63 entități.
- **Recomandare**: dacă nu ai nevoie de granularitate, folosește doar `60`. Dacă ai baterie/EV și vrei control precis, include `15`.

| Rezoluție | Intervale/zi | Granularitate | Recomandat pentru |
|-----------|-------------|---------------|-------------------|
| 15 | 96 | Foarte granular | Baterii, EV, pompe de căldură |
| 30 | 48 | Mediu | Uz general |
| 60 | 24 | Standard | Monitoring, dashboard |

#### Câmp 3: Zile în avans (`days_ahead`)

- **Ce face**: câte zile de date să descarce
- **Valori**: `1` (doar azi) sau `2` (azi + mâine)
- **Implicit**: `2`
- **Impact**: cu `1`, senzorii „mâine" nu vor avea date
- **Recomandare**: lasă pe `2`
- **Notă**: OPCOM nu publică niciodată date pentru D+2 (poimâine), de aceea valoarea maximă este 2

#### Câmp 4: Interval actualizare (`scan_interval_minutes`)

- **Ce face**: la câte minute se reîmprospătează datele de pe OPCOM
- **Interval permis**: 5–180 minute
- **Implicit**: `15` minute
- **Impact**: valori mici = mai multe request-uri către opcom.ro. Prețurile OPCOM se schimbă o dată pe zi, deci actualizare la fiecare 15 minute e mai mult decât suficient.
- **Recomandare**: lasă pe `15`. Mărește la `30` sau `60` dacă vrei să reduci traficul.

#### Câmp 5: Fereastră planificare (`window_minutes`)

- **Ce face**: durata ferestrei de preț optim, în minute
- **Minim**: 15 minute
- **Implicit**: `60` minute
- **Impact**: ferestre mai mari = prețuri medii mai stabile, dar pierd oportunități scurte. Ferestre mai mici = mai sensibil la variații de preț.
- **Exemple**: `60` = ferestre de 1 oră, `120` = ferestre de 2 ore, `30` = ferestre de 30 minute
- **Recomandare**: `60` pentru baterii standard. `120` dacă ai capacitate mare de stocare.

#### Câmp 6: Număr ferestre (`top_n_windows`) — implicit global

- **Ce face**: câte ferestre optime să calculeze (top N), valoare globală aplicată pe toate rezoluțiile
- **Interval permis**: 1–24
- **Implicit**: `6`
- **Notă**: dacă definești și Câmpul 7 (per rezoluție), valorile de acolo au prioritate

#### Câmp 7: Ferestre per rezoluție (`top_n_per_resolution`) — opțional

- **Ce face**: permite configurarea unui top N diferit pentru fiecare rezoluție
- **Format**: perechi `rezoluție:număr` separate prin virgulă. Exemplu: `15:4,30:6,60:2`
- **Implicit**: gol (se folosește valoarea globală din Câmpul 6)
- **Separatori acceptați**: virgulă, punct și virgulă, spațiu, pipe (`|`)

#### Câmp 8: Prag preț mic (`price_threshold_low`) — opțional

- **Ce face**: pragul maxim de preț pentru import/cumpărare (RON/MWh)
- **Interval permis**: -500 – 10000
- **Implicit**: gol (dezactivat)
- **Impact pe senzori**: Când e configurat, senzorul binar `Ar trebui să încarce acum` se activează doar dacă intervalul curent e într-o fereastră ieftină **ȘI** prețul curent ≤ acest prag. De asemenea, senzorul `Preț sub prag acum` se activează când prețul curent ≤ prag.
- **Exemplu**: setezi 400 → senzorul de încărcare nu se activează dacă prețul e 450 RON/MWh, chiar dacă e în top 6 cele mai ieftine ferestre
- **Gol**: senzorii funcționează fără filtru de preț (doar pe baza ferestrei)

#### Câmp 9: Prag preț mare (`price_threshold_high`) — opțional

- **Ce face**: pragul minim de preț pentru export/vânzare (RON/MWh)
- **Interval permis**: -500 – 10000
- **Implicit**: gol (dezactivat)
- **Impact pe senzori**: Când e configurat, senzorul binar `Ar trebui să exporte acum` se activează doar dacă intervalul curent e într-o fereastră scumpă **ȘI** prețul curent ≥ acest prag. De asemenea, senzorul `Preț peste prag acum` se activează când prețul curent ≥ prag.
- **Exemplu**: setezi 600 → senzorul de export nu se activează dacă prețul e 550 RON/MWh, chiar dacă e în top 6 cele mai scumpe ferestre
- **Gol**: senzorii funcționează fără filtru de preț

**⚠️ Atenție**: dacă setezi ambele praguri, pragul mic trebuie să fie strict mai mic decât pragul mare.

### Pasul 3 — Licență

Integrarea necesită o **licență validă** pentru a funcționa complet. La prima instalare ai o perioadă de evaluare (trial). După expirarea trial-ului, trebuie să activezi o licență.

Licențe disponibile la: [hubinteligent.org/licenta/opcom](https://hubinteligent.org/licenta/opcom)

Pentru a introduce licența:

1. **Settings** → **Devices & Services**
2. Găsește **OPCOM** → click pe **Configure**
3. Selectează **Licență**
4. Introdu cheia de licență
5. Click **Submit**

Fără licență validă:
- Se creează doar senzorul `Licență necesară` cu informații despre status și zile rămase
- Toți senzorii normali și binari sunt dezactivați

### Pasul 4 — Confirmă

Click **Submit**. Integrarea se instalează și creează:
- 1 device „OPCOM România"
- 63 entități (cu configurarea implicită de 3 rezoluții) — sau mai puține, în funcție de câte rezoluții ai ales

Prima actualizare durează câteva secunde (descarcă CSV-urile de pe OPCOM).

#### Cum calculezi corect: relația dintre fereastră și număr ferestre

Cele două câmpuri (`window_minutes` și `top_n_windows`) lucrează împreună. Formula:

```
Ore acoperite = top_n_windows × window_minutes ÷ 60
```

**Exemplu: am nevoie de 4 ore de cumpărare și 4 ore de vânzare**

| Setare | Valoare | De ce |
|--------|---------|-------|
| `window_minutes` | `60` | Fiecare fereastră = 1 oră |
| `top_n_windows` | `4` | 4 ferestre × 1 oră = **4 ore** |

**⚠️ Greșeală frecventă: `top_n_windows` prea mare**

Dacă setezi `top_n_windows = 16` cu `window_minutes = 60`:
- 16 ferestre × 60 min = **16 ore** marcate „ieftin"
- 16 ferestre × 60 min = **16 ore** marcate „scump"
- Ziua are doar 24 ore → minim 8 ore sunt ATÂT „ieftine" CÂT și „scumpe"
- Rezultat: senzorii binari de cumpărare ȘI vânzare sunt activi simultan → inutil

**Regulă practică:** `top_n_windows × window_minutes ÷ 60` nu ar trebui să depășească **8–10 ore**. Altfel, pierzi selectivitatea.

**Tabel rapid de referință:**

| Nevoie | `window_minutes` | `top_n_windows` | Ore acoperite |
|--------|-------------------|-----------------|---------------|
| 2 ore cumpărare/vânzare | 60 | 2 | 2h |
| 4 ore cumpărare/vânzare | 60 | 4 | 4h |
| 4 ore (ferestre de 30 min) | 30 | 8 | 4h |
| 6 ore cumpărare/vânzare | 60 | 6 | 6h |
| 8 ore (ferestre de 2h) | 120 | 4 | 8h |

---

## Reconfigurare (fără reinstalare)

Toate setările pot fi modificate din UI, fără a șterge și readăuga integrarea.

1. **Settings** → **Devices & Services**
2. Găsește **OPCOM** → click pe **Configure** (⚙️)
3. Selectează **Setări** (pentru parametrii OPCOM) sau **Licență** (pentru cheia de licență)
4. Modifică ce dorești
5. Click **Submit**
6. Integrarea se reîncarcă automat (nu e nevoie de restart)

**Atenție la schimbarea rezoluțiilor**: dacă adaugi/elimini rezoluții, entitățile corespunzătoare sunt create/eliminate automat.

---

## Verificare după instalare

### Verifică că device-ul există

1. **Settings** → **Devices & Services** → click pe **OPCOM**
2. Ar trebui să vezi device-ul „OPCOM România" cu 63 entități (sau mai puține, depinde de rezoluții)

### Verifică senzorii

1. **Developer Tools** → **States**
2. Filtrează după `opcom` sau `pret_acum`
3. Ar trebui să vezi entitățile cu valori numerice (ex: `322.56`)

### Verifică logurile (dacă ceva nu merge)

1. **Settings** → **System** → **Logs**
2. Caută mesaje cu `OPCOM`
3. Pentru detalii, activează debug logging — vezi [DEBUG.md](DEBUG.md)

---

## Dezinstalare

### Prin HACS

1. HACS → găsește „OPCOM România" → **Remove**
2. Restartează Home Assistant

### Manual

1. **Settings** → **Devices & Services** → OPCOM → **Delete**
2. Șterge folderul `config/custom_components/opcom/`
3. Restartează Home Assistant
