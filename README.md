# Smarte Bewässerung

Eine Home-Assistant-Integration (HACS), die den Wasserbedarf jeder Gartenzone nach **FAO-56** berechnet.
Sie läuft **parallel zu Smart Irrigation im Schattenbetrieb**: Sie schaltet keine Ventile und ändert nichts
an bestehenden Automationen. Sie beobachtet nur und gibt Empfehlungen als Sensoren aus. So kannst du ein paar
Tage lang vergleichen, bevor du ihr die Steuerung überlässt.

## Warum noch eine Integration neben Smart Irrigation?

| | Smart Irrigation | Smarte Bewässerung |
|---|---|---|
| Verdunstung | PyETO aus stündlichen Momentwerten | ET₀ (FAO-56) stündlich von Open-Meteo, Bedarf läuft über den Tag mit |
| Pflanzen | ein fester Multiplikator | Pflanzenprofil (Rasen, Bananen, Gemüse …) mit Kc je Monat: Frühjahrswachstum, Hochsommer, Winterruhe |
| Regen | je nach Mapping, Momentwerte können doppelt zählen | Tagessumme von Open-Meteo oder **eigener Regensensor** |
| Bodenmodell | Eimer mit max. Größe | Wurzelzone mit Bodenart, Wurzeltiefe, TAW/RAW, Versickerung |
| Echte Läufe | nur, wenn `linked_entity` oder ein Durchflusssensor gepflegt ist | erkennt jeden Ventillauf automatisch, auch die von Smart Irrigation |
| Wassermenge | — | summiert den Volumenstrom des Ventils (m³/h) über den Lauf; Mengenzähler oder Schätzung als Rückfall |
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

**Zone** (zwei Schritte):

1. **Was wird bewässert?** Pflanze, Fläche und Durchsatz.
2. **Details.** Die Werte sind aus dem Pflanzenprofil vorausgefüllt und lassen sich ändern.

| Feld | Bedeutung |
|---|---|
| Pflanze | bestimmt Kc-Jahresverlauf, Wurzeltiefe, Gießschwelle und Bewässerungsart |
| Anpassung Wasserbedarf | Faktor auf den Jahresverlauf: Schatten 0,8 · volle Sonne 1,1 · frisch gepflanzt 1,2 |
| Bodenart, Wurzeltiefe | nutzbares Bodenwasser (TAW) |
| Ausschöpfung p | Anteil von TAW, ab dem gegossen wird |
| Min./max. Laufzeit | |
| Ventil | wird nur **beobachtet** |
| Volumenstrom | z. B. `sensor.ventil_haus_flow` (m³/h): misst die Liter je Lauf, erkennt „offen ohne Wasser“. Ohne Ventil gilt „Volumenstrom > 0“ als Lauf |
| Mengenzähler | Rückfall, wenn kein Volumenstrom da ist |
| Bodenfeuchtesensor | mit Kalibrierwerten trocken/nass |
| Vergleich mit | z. B. `sensor.smart_irrigation_haus` |

### Pflanzenprofile

Der Pflanzenfaktor Kc gilt jeweils für die Monatsmitte. Dazwischen wird er Tag für Tag interpoliert, die Kurve
springt also nicht am Monatswechsel.

| Pflanze | Jan | Feb | Mär | Apr | Mai | Jun | Jul | Aug | Sep | Okt | Nov | Dez | Wurzel | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rasen | 0,60 | 0,64 | 0,75 | **1,00** | 0,95 | 0,88 | 0,85 | 0,82 | 0,80 | 0,72 | 0,65 | 0,60 | 20 cm | 0,5 |
| Spiel-/Sportrasen | 0,60 | 0,64 | 0,78 | **1,05** | 1,00 | 0,95 | 0,92 | 0,90 | 0,85 | 0,75 | 0,65 | 0,60 | 15 cm | 0,45 |
| Bananen | 0,20 | 0,20 | 0,30 | 0,60 | 0,95 | 1,15 | **1,20** | **1,20** | 1,05 | 0,75 | 0,30 | 0,20 | 40 cm | 0,35 |
| Gemüsebeet | 0,30 | 0,30 | 0,40 | 0,60 | 0,85 | 1,05 | **1,10** | 1,05 | 0,85 | 0,60 | 0,30 | 0,30 | 30 cm | 0,4 |
| Stauden/Blumen | 0,30 | 0,30 | 0,45 | 0,70 | 0,85 | **0,90** | **0,90** | 0,85 | 0,70 | 0,50 | 0,30 | 0,30 | 30 cm | 0,45 |
| Sträucher/Hecke | 0,30 | 0,30 | 0,40 | 0,55 | **0,60** | **0,60** | **0,60** | 0,55 | 0,50 | 0,40 | 0,30 | 0,30 | 50 cm | 0,5 |
| Obstbäume | 0,30 | 0,30 | 0,45 | 0,60 | 0,85 | **0,95** | **0,95** | **0,95** | 0,80 | 0,60 | 0,35 | 0,30 | 80 cm | 0,5 |
| Beerensträucher | 0,30 | 0,30 | 0,40 | 0,60 | 0,90 | **1,05** | **1,05** | 0,95 | 0,70 | 0,50 | 0,30 | 0,30 | 40 cm | 0,5 |

Quellen:
- **Rasen:** Kc-Monatswerte für Kühle-Saison-Rasen nach Meyer & Gibeault (University of California), an mitteleuropäische Wachstumsphasen angepasst. Der
  Spitzenbedarf liegt im **Frühjahrswachstum im April**; im Hochsommer ist der Bedarf je Grad Verdunstung etwas
  geringer. Die absolute Wassermenge ist im Sommer trotzdem am höchsten, weil ET₀ dann viel größer ist.
- **Bananen, Gemüse, Obst, Beeren:** Kc-Phasen nach FAO-56, Tab. 12, auf die Freiland-Saison in Deutschland gelegt.
  Bananen ruhen von November bis März.
- **Sträucher, Stauden:** WUCOLS, Stufe „mittlerer Bedarf“.

Wer keine dieser Pflanzen hat, wählt **„Eigener fester Kc“** und trägt einen festen Pflanzenfaktor ein.

### Vorschlag für die drei Zonen

| Zone | Pflanze | Fläche | Durchsatz | Volumenstrom | Vergleich |
|---|---|---|---|---|---|
| Volleyball | Spiel-/Sportrasen | 200 m² | 23 l/min | `sensor.ventil_volleyball_flow` | `sensor.smart_irrigation_volleyball` |
| Haus | Rasen | 120 m² | 17 l/min | `sensor.ventil_haus_flow` | `sensor.smart_irrigation_haus` |
| Bananen | Bananen | 15 m² | 20 l/min | `sensor.ventil_bananen_flow` | `sensor.smart_irrigation_bananen` |

Bei Smart Irrigation haben die Bananen einen Multiplikator von 3. Das entspricht keinem realistischen Kc. Das
Bananenprofil kommt im Hochsommer auf 1,2 und rechnet mit 40 cm Wurzeltiefe.

Als Mengenzähler eignet sich `real_time_irrigation_volume` bei diesen Ventilen nicht: Er steht auf 16777 l, was
nach einem Überlaufwert aussieht. Der Volumenstrom (z. B. 1,3 m³/h ≈ 21,7 l/min) passt dagegen gut zum
eingestellten Durchsatz.

## Entities

**Gerät „Smarte Bewässerung“:** ET₀ heute/gestern, Regen heute/gestern (mit Quelle), Regenvorhersage 24 h,
Tiefsttemperatur 24 h, Frostgefahr, Regensperre.

**Je Zone:**

Wasserbedarf, laufend aktualisiert (alle 5 Minuten und bei jeder Ventil- oder Sensoränderung):
- **Wasserbedarf (l):** so viel Wasser fehlt **jetzt**, um den Boden wieder auf Feldkapazität zu bringen
- **Erschöpfung (mm)** und **Bodenwasser (%)**
- **Verbrauch bisher heute**, **Verbrauch heute (Prognose)**, **Verbrauch morgen (Prognose)** und **Verbrauch 7 Tage** (mm)

Jahreszeit:
- **Pflanzenfaktor** (Kc des Tages)
- **Saisonphase:** Winterruhe, Austrieb, Wachstum, Hauptsaison oder Abreife

Empfehlung:
- **Empfohlene Dauer** (min) und **Empfohlene Menge** (l)
- **Begründung**
- **Bewässerung empfohlen**

Läufe:
- **Bewässert gerade**
- **Laufender Lauf (l):** steigt live mit dem Volumenstrom
- **Letzter Lauf (l):** mit Quelle Volumenstrom, Zähler oder Schätzung
- **Gemessener Durchsatz**
- **Wasser gesamt** (für das Energie-Dashboard)

Kontrolle:
- **Störung**, mit Liste der Ursachen
- **Abweichung zum Vergleich** (min)

Während eines Laufs sinkt die Erschöpfung live mit. Am Abend steigt sie mit der Verdunstung des Tages.

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
Jederzeit (laufender Tag):
  Kc(heute)  = Pflanzenprofil, zwischen Monatsmitten interpoliert × Anpassung
  ETc_bisher = ET₀ seit Mitternacht (stündlich, Open-Meteo) × Kc(heute)
  Regen_eff  = max(0, Regen seit Mitternacht − 0,5 mm) × 0,9
  Dr         = clamp(Dr_Tagesbeginn + ETc_bisher − Regen_eff − laufender Lauf, 0, TAW)
  Bedarf (l) = Dr / Wirkungsgrad × Fläche

Lauf beendet:
  Liter      = ∫ Volumenstrom dt   (sonst Zähler, sonst Laufzeit × Durchsatz)
  Dr         = max(0, Dr − Liter / Fläche × Wirkungsgrad)   # Überschuss versickert

Tagesabschluss (00:05):
  derselbe Wert mit dem vollen Tag → Dr_Tagesbeginn für morgen
  (optional) gemischt mit dem Bodenfeuchtesensor

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
