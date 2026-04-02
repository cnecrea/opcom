# Ghid de debugging — OPCOM România

Acest ghid explică cum activezi logarea detaliată, ce mesaje să cauți, și cum interpretezi fiecare situație.

---

## 1. Activează debug logging

Editează `configuration.yaml` și adaugă:

```yaml
logger:
  default: warning
  logs:
    custom_components.opcom: debug
```

Restartează Home Assistant (**Settings** → **System** → **Restart**).

Pentru a reduce zgomotul din loguri, poți adăuga:

```yaml
logger:
  default: warning
  logs:
    custom_components.opcom: debug
    homeassistant.const: critical
    homeassistant.loader: critical
    homeassistant.helpers.frame: critical
```

**Important**: dezactivează debug logging după ce ai rezolvat problema (setează `custom_components.opcom: info` sau șterge blocul). Logarea debug generează mult text.

---

## 2. Unde găsești logurile

### Din UI

**Settings** → **System** → **Logs** → filtrează după `opcom`

### Din fișier

```bash
# Calea implicită
cat config/home-assistant.log | grep -i opcom

# Sau doar erorile
grep -E "(ERROR|WARNING).*opcom" config/home-assistant.log

# Ultimele 100 linii OPCOM
grep -i opcom config/home-assistant.log | tail -100
```

### Din terminal (Docker/HAOS)

```bash
# Docker
docker logs homeassistant 2>&1 | grep -i opcom

# Home Assistant OS (SSH add-on)
ha core logs | grep -i opcom
```

---

## 3. Mesajele de la pornire

La prima pornire a integrării (sau după restart), ar trebui să vezi:

```
[OPCOM] Inițializez LicenseManager (prima entry)
[OPCOM] LicenseManager: status=trial, valid=True, fingerprint=abcdef1234...
[OPCOM] Programez heartbeat periodic la fiecare 86400 secunde (24 ore)
[OPCOM] Perioadă de evaluare — 14 zile rămase
```

Sau, cu licență activă:

```
[OPCOM] LicenseManager: status=licensed, valid=True, fingerprint=abcdef1234...
[OPCOM] Licență activă — tip: perpetual
```

Apoi setările integrării:

```
OPCOM: setări citite (surse: lang=data, res=data, days=data, scan=data, window=data, top_n=data, top_n_per_res=default, prag_low=default, prag_high=default).
Rezultat: OpcomSettings(lang='ro', resolutions=[15, 30, 60], days_ahead=2, scan_interval_minutes=15, window_minutes=60, top_n_windows=6, top_n_per_res={}, price_threshold_low=None, price_threshold_high=None)
```

```
OPCOM: coordinator pornit (entry=01KHYVQ0V...). Fallback polling=15 min, timer graniță=15 min.
```

**Ce înseamnă sursele**:
- `data` = valoarea vine din configurarea inițială
- `options` = valoarea vine din reconfigurare (Options flow)
- `default` = nu s-a găsit nici în data, nici în options — se folosește valoarea implicită

---

## 4. Ciclul normal de actualizare

La fiecare ciclu (implicit la 15 minute), ar trebui să vezi:

```
OPCOM: încep fetch. Data=2026-04-03 (CET/CEST). Zile=2, rezoluții=[15, 30, 60], limba='ro'.
OPCOM: încep ziua 2026-04-03 (rezoluții=[15, 30, 60], lang=ro).
OPCOM: descarc CSV (data=2026-04-03, res=15, lang=ro).
OPCOM: CSV ok (data=2026-04-03, res=15). Prima linie: "PIP si volum tranzactionat..."
OPCOM: intervale parse-uite (data=2026-04-03, res=15): count=96, fara_interval=0, fara_pret=0.
OPCOM: parse final (data=2026-04-03, res=15). Are summary=da. Intervale=96.
```

Se repetă pentru fiecare rezoluție (30, 60) și apoi pentru ziua 2 (mâine).

La final:

```
OPCOM: actualizare completă (0.32s, încercare 1/3, v12). Zile: ['2026-04-03', '2026-04-04']
```

**Dacă vezi asta, totul funcționează corect.**

---

## 5. Situații normale (nu sunt erori)

### CSV gol pentru ziua de mâine

```
OPCOM: CSV gol (data=2026-04-04, res=15).
OPCOM: parse final (data=2026-04-04, res=15). Are summary=nu. Intervale=0.
```

**Cauza**: OPCOM nu a publicat încă prețurile pentru mâine. Se publică de obicei între 13:00–15:00 CET. Senzorii „mâine" afișează „Necunoscut". Complet normal.

### O zi eșuează, restul funcționează

```
OPCOM: nu am putut descărca ziua 2026-04-04 (0.85s, offset=1): RuntimeError: Toate rezoluțiile ([15, 30, 60]) au eșuat. Senzorii pentru această zi vor afișa Unknown.
OPCOM: actualizare completă (1.12s, încercare 1/3, v13). Zile: ['2026-04-03']
```

**Cauza**: ziua de mâine nu are date (normal înainte de ~14:00 CET) sau eroare de rețea temporară. Senzorii de azi funcționează normal — izolarea per zi protejează datele existente.

### O rezoluție eșuează, restul funcționează

```
OPCOM: fetch CSV eșuat (data=2026-04-03, res=30): RuntimeError: Nu pot descărca CSV...
OPCOM: ziua 2026-04-03 — rezoluții eșuate: [30], reușite: [15, 60].
```

**Cauza**: eroare temporară pe o singură rezoluție. Celelalte rezoluții funcționează normal.

### Titlu negăsit

```
OPCOM: nu am găsit titlu pentru ziua 2026-04-04 (ok, nu e critic).
```

**Cauza**: ziua nu are date. Nu afectează funcționalitatea.

### Timer graniță

```
OPCOM: timer graniță programat la 2026-04-03 12:15:00+02:00 (rezoluție=15 min).
OPCOM: timer graniță a tras la 2026-04-03 12:15:00+02:00.
```

**Cauza**: timer-ul de sincronizare exactă cu schimbarea intervalului. Se reprogramează automat după fiecare execuție.

---

## 6. Situații de eroare

### Eroare de rețea / timeout

```
OPCOM: eroare la încercare 1/3: RuntimeError: Nu pot descărca CSV... Reîncerc în 5 secunde.
OPCOM: eroare la încercare 2/3: RuntimeError: Nu pot descărca CSV... Reîncerc în 10 secunde.
OPCOM: toate cele 3 încercări au eșuat (45.12s). Ultima eroare: RuntimeError: ...
```

**Cauza**: opcom.ro nu răspunde sau conexiunea HA la internet e întreruptă. Integrarea reîncearcă automat cu backoff exponențial (5s, 10s, 20s).

**Rezolvare**: verifică conexiunea la internet, verifică dacă opcom.ro e accesibil din browser. Integrarea reîncearcă automat la următorul ciclu de polling.

### Header CSV neașteptat

```
OPCOM: header de intervale neașteptat (data=2026-04-03, res=15). Header=[...]. Eroare=Header CSV neașteptat / incomplet (lipsesc Interval/Pret): [...]
```

**Cauza**: OPCOM a schimbat formatul exportului CSV.

**Rezolvare**: deschide un [issue pe GitHub](https://github.com/cnecrea/opcom/issues) cu logul complet + diagnostics.

### Intervale fără preț

```
OPCOM: intervale parse-uite (data=2026-04-03, res=15): count=96, fara_interval=0, fara_pret=3.
```

**Cauza**: 3 intervale din CSV nu aveau preț. Intervalele sunt păstrate (cu preț `null`), dar ferestrele care le includ sunt ignorate.

### Licență invalidă

```
[OPCOM] Integrarea nu are licență validă. Senzorii vor afișa 'Licență necesară'.
```

**Cauza**: licența a expirat, nu a fost introdusă, sau serverul de licențe nu e accesibil.

**Rezolvare**: verifică licența din **Configure** → **Licență**. Dacă ai licență activă dar vezi acest mesaj, verifică conexiunea la internet (serverul de licențe trebuie să fie accesibil).

### Tranziție licență

```
[OPCOM] Licența a devenit invalidă — reîncarc senzorii
[OPCOM] Licența a redevenit validă — reîncarc senzorii
```

**Cauza**: heartbeat-ul a detectat o schimbare de stare a licenței. Integrarea se reîncarcă automat — senzorii normali sunt înlocuiți cu `Licență necesară` (sau invers).

---

## 7. Loguri licență (heartbeat)

```
[OPCOM] Heartbeat: cache expirat, verific la server
[OPCOM] Heartbeat: reprogramez la 86400 secunde (1440 min)
```

Normal — verificare periodică a licenței la server. Intervalul vine de la server (tipic 24h).

```
[OPCOM] Heartbeat: cache valid, nu e nevoie de verificare
```

Normal — cache-ul de licență e încă valid.

```
[OPCOM] Cache expirat — verific imediat la server
```

Normal — timer-ul precis de cache expiry s-a declanșat.

---

## 8. Diagnostics

Pentru raportare de probleme, exportă diagnostics-ul:

1. **Settings** → **Devices & Services** → OPCOM
2. Click pe cele 3 puncte (⋮) → **Download diagnostics**
3. Atașează fișierul JSON la issue

Diagnostics-ul conține: configurația completă (entry.data + entry.options), lista tuturor entităților, toate datele raw descărcate (prețuri, volume, sumare), starea ultimei actualizări.

**Atenție**: diagnostics-ul NU conține date personale, credențiale, sau chei de licență. E safe de postat public.

---

## 9. Cum raportezi un bug

1. Activează debug logging (secțiunea 1)
2. Reproduce problema
3. Exportă diagnostics (secțiunea 8)
4. Deschide un [issue pe GitHub](https://github.com/cnecrea/opcom/issues) cu:
   - **Descrierea problemei** — ce ai așteptat vs. ce s-a întâmplat
   - **Logurile relevante** — filtrează după `opcom` și include 20–50 linii relevante
   - **Fișierul diagnostics** — atașează JSON-ul
   - **Versiunea HA** — din **Settings** → **About**
   - **Versiunea integrării** — din **Settings** → **Devices & Services** → OPCOM

### Cum postezi loguri pe GitHub

Folosește blocuri de cod delimitate de 3 backticks:

````
```
2026-04-03 12:06:06.818 DEBUG custom_components.opcom.coordinator OPCOM: setări citite ...
2026-04-03 12:06:06.837 DEBUG custom_components.opcom.coordinator OPCOM: coordinator pornit ...
```
````

Dacă logul e foarte lung (peste 50 linii), folosește secțiunea colapsabilă:

````
<details>
<summary>Log complet (click pentru a expanda)</summary>

```
... logul aici ...
```

</details>
````
