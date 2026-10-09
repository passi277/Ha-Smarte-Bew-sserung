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
    ),
    # Gemüsebeet: FAO-56 Kc ini 0,5 / mid 1,05 / end 0,8.
    "vegetables": PlantProfile(
        (0.30, 0.30, 0.40, 0.60, 0.85, 1.05, 1.10, 1.05, 0.85, 0.60, 0.30, 0.30),
        (D, D, S, S, G, P, P, P, R, R, D, D),
        30,
        0.4,
        "drip",
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
