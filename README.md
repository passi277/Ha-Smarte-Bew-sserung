# Smarte Bewässerung

Eine Home-Assistant-Integration (HACS), die den Wasserbedarf jeder Gartenzone nach **FAO-56** berechnet.
Sie läuft **parallel zu Smart Irrigation im Schattenbetrieb**: Sie schaltet keine Ventile und ändert nichts
an bestehenden Automationen. Sie beobachtet nur und gibt Empfehlungen als Sensoren aus. So kannst du ein paar
Tage lang vergleichen, bevor du ihr die Steuerung überlässt.

## Warum noch eine Integration neben Smart Irrigation?

| | Smart Irrigation | Smarte Bewässerung |
|---|---|---|
| Verdunstung | PyETO aus stündlichen Momentwerten | ET₀ (FAO-56) als Tageswert direkt von Open-Meteo |
| Regen | je nach Mapping, Momentwerte können doppelt zählen | Tagessumme von Open-Meteo oder **eigener Regensensor** |
| Bodenmodell | Eimer mit max. Größe | Wurzelzone mit Bodenart, Wurzeltiefe, TAW/RAW, Versickerung |
| Echte Läufe | nur, wenn `linked_entity` oder ein Durchflusssensor gepflegt ist | erkennt jeden Ventillauf automatisch, auch die von Smart Irrigation |
| Wassermenge | — | misst Liter am Ventilzähler, verwirft unplausible Werte und schätzt dann |
| Bodenfeuchte | Schwelle | mischt Sensor und Modell, mit Kalibrierung trocken/nass |
| Begründung | lange Formel | ein Satz: „Defizit 16 mm (Schwelle 15 mm) → 30 min / 510 l“ |
| Störungen | — | Ventil offline, offen ohne Durchfluss, hängt offen, Durchsatz weicht ab |
| Statistik | — | Liter gesamt (Energie-Dashboard „Wasser“), 14-Tage-Verlauf je Zone |
| Ausfall von HA | — | holt verpasste Tage nach (bis 7 Tage zurück) |

## Installation

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/passi277/Ha-Smarte-Bew-sserung`, Typ *Integration*.
2. „Smarte Bewässerung“ installieren und Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Smarte Bewässerung*.
4. In der Integration über **„Zone hinzufügen“** jede Zone anlegen.

Erst ab Home Assistant 2025.4 nutzbar, weil Zonen als Subentries angelegt werden.

## Einstellungen

**Integration** (später über *Konfigurieren* änderbar):

- **Regensensor** (optional) kann sein:
  - ein Mengenzähler in mm, der dann den Open-Meteo-Regen ersetzt,
  - ein Intensitätssensor in mm/h oder ein binärer Sensor, die dann nur als Sperre „regnet gerade“ wirken.
- **Regensperre**: Ab dieser Regenvorhersage für die nächsten 24 h wird nicht gegossen (Standard 3 mm).
- **Frostsperre** (4 °C) und **Windsperre** (30 km/h).
- **Gewicht Bodenfeuchtesensor**: Beim Tagesabschluss werden Modell und Sensor gemischt (0,5 = halb/halb).

**Zone:**

| Feld | Bedeutung | Richtwert |
|---|---|---|
| Fläche, Durchsatz | daraus folgt die Niederschlagsrate | aus der Smart-Irrigation-Zone übernehmen |
| Bewässerungsart | Regner 75 %, Tropfer 90 % Wirkungsgrad | |
| Kc | Pflanzenfaktor | Rasen 0,8 · Bananen 1,1–1,2 |
| Bodenart, Wurzeltiefe | nutzbares Bodenwasser (TAW) | Lehm, Rasen 20 cm, Bananen 40–60 cm |
| Ausschöpfung p | Anteil von TAW, ab dem gegossen wird | 0,5 |
| Min./max. Laufzeit | | 3 / 30 min |
| Ventil | wird nur **beobachtet** | `switch.ventil_haus` o. ä. |
| Mengenzähler | Liter je Lauf | `sensor.ventil_haus_real_time_irrigation_volume` |
| Durchflusssensor | erkennt „offen, aber kein Wasser“ | optional |
| Bodenfeuchtesensor | mit Kalibrierwerten trocken/nass | optional |
| Vergleich mit | z. B. `sensor.smart_irrigation_haus` | optional |

### Vorschlag für die drei Zonen

| Zone | Fläche | Durchsatz | Art | Kc | Wurzeltiefe | Vergleich |
|---|---|---|---|---|---|---|
| Volleyball | 200 m² | 23 l/min | Regner | 0,8 | 20 cm | `sensor.smart_irrigation_volleyball` |
| Haus | 120 m² | 17 l/min | Regner | 0,8 | 20 cm | `sensor.smart_irrigation_haus` |
| Bananen | 15 m² | 20 l/min | Regner/Tropfer prüfen | 1,2 | 40 cm | `sensor.smart_irrigation_bananen` |

Bei Smart Irrigation haben die Bananen einen Multiplikator von 3. Das entspricht keinem realistischen Kc und
erzeugt Defizite bis −19 mm am Tag. Hier reicht 1,2 zusammen mit einer größeren Wurzeltiefe.

## Entities

**Gerät „Smarte Bewässerung“:** ET₀ heute/gestern, Regen heute/gestern (mit Quelle), Regenvorhersage 24 h,
Tiefsttemperatur 24 h, Frostgefahr, Regensperre.

**Je Zone:**

- Erschöpfung (mm), mit TAW, RAW und 14-Tage-Verlauf als Attribute
- Bodenwasser (%)
- Empfohlene Dauer (min) und Empfohlene Menge (l)
- Begründung
- Bewässerung empfohlen
- Letzter Lauf (l), mit Start, Dauer und Angabe, ob gemessen oder geschätzt
- Gemessener Durchsatz (Median der letzten 5 Läufe)
- Wasser gesamt (für das Energie-Dashboard)
- Bewässert gerade
- Störung, mit Liste der Ursachen
- Abweichung zum Vergleich (min): positiv heißt, diese Integration würde länger gießen als Smart Irrigation

## Services

| Service | Zweck |
|---|---|
| `smarte_bewaesserung.recalculate` | Wetter neu laden und alles neu berechnen |
| `smarte_bewaesserung.record_irrigation` | Lauf ohne Ventil eintragen (Liter oder Minuten) |
| `smarte_bewaesserung.set_depletion` | Wasserkonto setzen (mm oder % Bodenwasser) |
| `smarte_bewaesserung.calibrate_soil_sensor` | aktuellen Bodenfeuchtewert als „trocken“ oder „nass“ speichern |

Für `entity_id` reicht eine beliebige Entity der Zone, z. B. ihr Sensor „Erschöpfung“.

## So wird gerechnet

```
Tagesabschluss (00:05 für den Vortag):
  ETc        = ET₀ × Kc
  Regen_eff  = max(0, Regen − 0,5 mm) × 0,9
  Dr         = clamp(Dr + ETc − Regen_eff, 0, TAW)        # Überschuss versickert
  (optional) Dr = Dr × (1 − w) + Dr_Sensor × w

Bei jedem Ventillauf sofort:
  Dr         = Dr − Liter / Fläche × Wirkungsgrad

Empfehlung:
  gießen, wenn Dr ≥ RAW (= p × TAW) und keine Sperre greift
  Ziel       = Dr − Regen_eff(Vorhersage 24 h)
  Dauer      = Ziel / (Durchsatz / Fläche × Wirkungsgrad), begrenzt auf min/max
```

Beim ersten Start beginnt das Wasserkonto bei 0 (Boden voll). Bei Trockenheit setzt du es mit `set_depletion` auf
einen realistischen Wert.

## Bodenfeuchtesensor kalibrieren

1. An einem trockenen Tag vor dem Gießen `calibrate_soil_sensor` mit `point: dry` aufrufen.
2. Etwa einen Tag nach kräftigem Gießen oder Regen `point: wet` aufrufen.

Bis dahin gelten die Werte aus der Zone (Standard 10 % / 40 %).

## Entwicklung

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```
