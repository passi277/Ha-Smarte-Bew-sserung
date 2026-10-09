"""Pflanzenprofile mit jahreszeitlichem Pflanzenfaktor (Kc).

Monatswerte gelten für die Monatsmitte und werden dazwischen täglich linear
interpoliert. Grundlage:
- Rasen: Kc-Monatswerte für Kühle-Saison-Rasen (Meyer & Gibeault, University of
  California), an mitteleuropäische Wachstumsphasen angepasst: kräftiger
  Frühjahrsschub April/Mai, Sommerplateau, zweiter Schub im September.
- Bananen, Gemüse, Obst, Beeren: Kc-Phasen (Anfang/Mitte/Ende) nach FAO-56,
  Tab. 12, auf die Freiland-Saison in Deutschland gelegt. Bananen im Freien
  ruhen von November bis März (eingepackt oder eingeräumt).
- Sträucher, Stauden: WUCOLS-Einstufung „mittlerer Wasserbedarf“.

Wetterabhängige Phasen (adjust_for_weather):
- Frühjahr: Vegetationsbeginn, sobald die Grünlandtemperatursumme (GTS) den
  Schwellwert der Pflanze erreicht. GTS = Summe der positiven Tagesmittel ab
  1. Januar, Januar × 0,5, Februar × 0,75, ab März × 1. GTS 200 ist der
  übliche Vegetationsbeginn für Grünland (Deutscher Wetterdienst).
- Herbst: Saisonende nach DORMANCY_DAYS Tagen in Folge (ab August) mit
  Tagesmittel unter der Ruhetemperatur der Pflanze.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

PHASE_DORMANT = "dormant"
PHASE_SPROUTING = "sprouting"
PHASE_GROWTH = "growth"
PHASE_PEAK = "peak"
PHASE_RIPENING = "ripening"

D, S, G, P, R = PHASE_DORMANT, PHASE_SPROUTING, PHASE_GROWTH, PHASE_PEAK, PHASE_RIPENING


@dataclass(frozen=True)
class PlantProfile:
    """Eigenschaften einer Pflanzengruppe."""

    kc_monthly: tuple[float, ...]  # Jan … Dez
    phases: tuple[str, ...]  # Jan … Dez
    root_depth_cm: float
    depletion_fraction: float
    irrigation_type: str
    # Vegetationsbeginn: Grünlandtemperatursumme (°C), ab der die Pflanze austreibt.
    spring_gts: float = 200.0
    # Saisonende: so viele Tage in Folge mit Tagesmittel unter dieser Temperatur.
    dormancy_temp_c: float = 5.0


PLANTS: dict[str, PlantProfile] = {
    # Kühle-Saison-Rasen: Spitzenbedarf im Frühjahrswachstum, im Hochsommer etwas weniger.
    "lawn": PlantProfile(
        (0.60, 0.64, 0.75, 1.00, 0.95, 0.88, 0.85, 0.82, 0.80, 0.72, 0.65, 0.60),
        (D, D, S, G, G, P, P, P, G, R, D, D),
        20,
        0.5,
        "sprinkler",
    ),
    # Gebrauchsrasen/Spielfläche: wie Rasen, wegen Belastung etwas höher.
    "sports_lawn": PlantProfile(
        (0.60, 0.64, 0.78, 1.05, 1.00, 0.95, 0.92, 0.90, 0.85, 0.75, 0.65, 0.60),
        (D, D, S, G, G, P, P, P, G, R, D, D),
        15,
        0.45,
        "sprinkler",
    ),
    # Bananen (Musa basjoo im Freiland): FAO-56 Kc ini 1,0 / mid 1,2 / end 1,1 (2. Jahr).
    "banana": PlantProfile(
        (0.20, 0.20, 0.30, 0.60, 0.95, 1.15, 1.20, 1.20, 1.05, 0.75, 0.30, 0.20),
        (D, D, D, S, G, P, P, P, R, R, D, D),
        40,
        0.35,
        "drip",
        spring_gts=500,
        dormancy_temp_c=10,
    ),
    # Gemüsebeet: FAO-56 Kc ini 0,5 / mid 1,05 / end 0,8.
    "vegetables": PlantProfile(
        (0.30, 0.30, 0.40, 0.60, 0.85, 1.05, 1.10, 1.05, 0.85, 0.60, 0.30, 0.30),
        (D, D, S, S, G, P, P, P, R, R, D, D),
        30,
        0.4,
        "drip",
        spring_gts=300,
        dormancy_temp_c=8,
    ),
    # Stauden- und Blumenbeete.
    "perennials": PlantProfile(
        (0.30, 0.30, 0.45, 0.70, 0.85, 0.90, 0.90, 0.85, 0.70, 0.50, 0.30, 0.30),
        (D, D, S, G, G, P, P, P, R, R, D, D),
        30,
        0.45,
        "drip",
    ),
    # Sträucher und Hecken.
    "shrubs": PlantProfile(
        (0.30, 0.30, 0.40, 0.55, 0.60, 0.60, 0.60, 0.55, 0.50, 0.40, 0.30, 0.30),
        (D, D, S, G, G, P, P, P, R, R, D, D),
        50,
        0.5,
        "drip",
    ),
    # Obstbäume: FAO-56 Apfel/Kirsche Kc ini 0,6 / mid 0,95 / end 0,75.
    "fruit_trees": PlantProfile(
        (0.30, 0.30, 0.45, 0.60, 0.85, 0.95, 0.95, 0.95, 0.80, 0.60, 0.35, 0.30),
        (D, D, S, S, G, P, P, P, R, R, D, D),
        80,
        0.5,
        "drip",
        spring_gts=250,
    ),
    # Beerensträucher: FAO-56 Kc ini 0,3 / mid 1,05 / end 0,5.
    "berries": PlantProfile(
        (0.30, 0.30, 0.40, 0.60, 0.90, 1.05, 1.05, 0.95, 0.70, 0.50, 0.30, 0.30),
        (D, D, S, G, G, P, P, R, R, R, D, D),
        40,
        0.5,
        "drip",
    ),
}

PLANT_CUSTOM = "custom"
PLANT_TYPES = [*PLANTS, PLANT_CUSTOM]


def _month_mid(year: int, month: int) -> date:
    return date(year, month, 15)


def seasonal_kc(plant: str, day: date, custom_kc: float = 0.8) -> float:
    """Pflanzenfaktor für einen Tag (zwischen den Monatsmitten interpoliert)."""
    profile = PLANTS.get(plant)
    if profile is None:
        return custom_kc
    values = profile.kc_monthly
    mid = _month_mid(day.year, day.month)
    if day >= mid:
        start, start_value = mid, values[day.month - 1]
        nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 15)
        end, end_value = nxt, values[day.month % 12]
    else:
        prev_month = (day.month - 2) % 12 + 1
        start = date(day.year - (day.month == 1), prev_month, 15)
        start_value = values[prev_month - 1]
        end, end_value = mid, values[day.month - 1]
    fraction = (day - start).days / (end - start).days
    return round(start_value + (end_value - start_value) * fraction, 3)


def phase(plant: str, day: date) -> str | None:
    """Wachstumsphase des Monats."""
    profile = PLANTS.get(plant)
    return profile.phases[day.month - 1] if profile else None


DORMANCY_DAYS = 5
SOURCE_CALENDAR = "calendar"
SOURCE_WEATHER = "weather"


def gts_weight(month: int) -> float:
    """Monatsgewicht der Grünlandtemperatursumme."""
    return {1: 0.5, 2: 0.75}.get(month, 1.0)


def grassland_temperature_sum(daily_means: dict[date, float], day: date) -> float | None:
    """GTS vom 1. Januar bis zum Vortag; None, wenn mehr als 3 Tage fehlen."""
    total = 0.0
    missing = 0
    current = date(day.year, 1, 1)
    while current < day:
        mean = daily_means.get(current)
        if mean is None:
            missing += 1
        else:
            total += max(mean, 0.0) * gts_weight(current.month)
        current = date.fromordinal(current.toordinal() + 1)
    if missing > 3:
        return None
    return round(total, 1)


def weather_dormant(plant: str, daily_means: dict[date, float], day: date) -> bool | None:
    """Ruht die Pflanze laut Wetter? None, wenn die Daten nicht reichen."""
    profile = PLANTS.get(plant)
    if profile is None:
        return None
    if day.month <= 7:
        gts = grassland_temperature_sum(daily_means, day)
        return None if gts is None else gts < profile.spring_gts
    # Ab August: einmal DORMANCY_DAYS kalte Tage in Folge → Saisonende bis Jahresende.
    run = 0
    current = date(day.year, 8, 1)
    seen = 0
    while current < day:
        mean = daily_means.get(current)
        if mean is not None:
            seen += 1
            run = run + 1 if mean < profile.dormancy_temp_c else 0
            if run >= DORMANCY_DAYS:
                return True
        current = date.fromordinal(current.toordinal() + 1)
    return False if seen else None


def _edge_kc(profile: PlantProfile, spring: bool) -> float:
    """Kc des ersten (Frühjahr) bzw. letzten (Herbst) aktiven Monats."""
    months = range(12) if spring else range(11, -1, -1)
    for i in months:
        if profile.phases[i] != PHASE_DORMANT:
            return profile.kc_monthly[i]
    return min(profile.kc_monthly)


def adjust_for_weather(
    plant: str, day: date, dormant: bool | None, custom_kc: float = 0.8
) -> tuple[float, str | None, str]:
    """(Kc, Phase, Quelle) für einen Tag, korrigiert um das tatsächliche Wetter.

    Ohne Wetterdaten gilt der Kalender. Ruht die Pflanze laut Wetter, gilt der
    Ruhe-Kc; ist sie laut Wetter aktiv, obwohl der Kalender noch/schon Ruhe
    sagt, gilt Austrieb (Frühjahr) bzw. Abreife (Herbst) mit dem Kc des
    angrenzenden aktiven Monats.
    """
    profile = PLANTS.get(plant)
    kc = seasonal_kc(plant, day, custom_kc)
    calendar_phase = phase(plant, day)
    if profile is None or dormant is None:
        return kc, calendar_phase, SOURCE_CALENDAR
    if dormant:
        return min(profile.kc_monthly), PHASE_DORMANT, SOURCE_WEATHER
    if calendar_phase == PHASE_DORMANT:
        spring = day.month <= 7
        return (
            max(kc, _edge_kc(profile, spring)),
            PHASE_SPROUTING if spring else PHASE_RIPENING,
            SOURCE_WEATHER,
        )
    return kc, calendar_phase, SOURCE_WEATHER
