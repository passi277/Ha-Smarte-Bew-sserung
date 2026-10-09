"""Selbstkalibrierung des Bodenfeuchtesensors und Lernen des Wasserbedarfs.

Beides ist bewusst vorsichtig: Es wird nur aus eindeutigen Situationen gelernt,
und gelernte Werte ändern sich langsam.
"""

from __future__ import annotations

from statistics import median
from typing import Any

# Anstieg der Bodenfeuchte (Prozentpunkte gegenüber dem Minimum der letzten Stunden),
# ab dem ein Bewässerungs- oder Regenereignis erkannt wird.
WETTING_RISE_PCT = 3.0
WETTING_WINDOW_S = 6 * 3600
RISE_NOISE_PCT = 0.2
VALID_READING = (0.5, 100.0)
# Nach so langer Zeit ohne neuen Anstieg ist das Sickerwasser abgeflossen: Feldkapazität.
SETTLE_S = 24 * 3600
FC_CANDIDATES = 5
MIN_CALIBRATION_GAP_PCT = 5.0

# Lernen des Wasserbedarfs
LEARN_RATE = 0.15
LEARN_MIN_ETC_MM = 1.0
LEARN_RATIO_RANGE = (0.3, 2.0)
LEARN_FACTOR_RANGE = (0.5, 1.5)
LEARN_MIN_SAMPLES = 3
LEARN_VALID_RANGE = (0.05, 0.9)  # Anteil von TAW, in dem der Sensor aussagekräftig ist


class SoilCalibrator:
    """Lernt Feldkapazität („nass“) und Trockenwert aus dem Verlauf der Bodenfeuchte.

    Feldkapazität: Nach einem Anstieg (Regen, Gießen) fällt der Wert erst schnell,
    weil Sickerwasser abfließt, und pendelt sich dann ein. Der Wert 24 Stunden
    nach dem letzten Anstieg ist ein Kandidat; der Median der letzten Kandidaten
    gilt als „nass“.
    Trockenwert: der niedrigste je gemessene Wert, sofern er unter dem
    eingestellten Trockenwert liegt.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        state.setdefault("samples", [])
        state.setdefault("event_peak", None)
        state.setdefault("fc_candidates", [])
        state.setdefault("min_seen", None)

    def feed(self, ts: float, value: float) -> bool:
        """Messwert verarbeiten; True, wenn ein neuer Feldkapazitäts-Kandidat entstand."""
        if not VALID_READING[0] < value <= VALID_READING[1]:
            return False  # Aussetzer (0 %) oder Unsinn nicht lernen
        samples: list[list[float]] = self.state["samples"]
        previous = samples[-1][1] if samples else None
        samples.append([ts, value])
        samples[:] = [s for s in samples if ts - s[0] <= WETTING_WINDOW_S]
        min_seen = self.state["min_seen"]
        self.state["min_seen"] = value if min_seen is None else min(min_seen, value)

        recent_min = min(v for _, v in samples)
        rising = previous is not None and value > previous + RISE_NOISE_PCT
        if rising and value - recent_min >= WETTING_RISE_PCT:
            # Neues oder weiter steigendes Nässe-Ereignis: Zeitpunkt des letzten Anstiegs merken.
            self.state["event_peak"] = [ts, value]
            return False
        peak = self.state["event_peak"]
        if peak is not None and ts - peak[0] >= SETTLE_S:
            candidates: list[float] = self.state["fc_candidates"]
            candidates.append(round(value, 1))
            del candidates[:-FC_CANDIDATES]
            self.state["event_peak"] = None
            return True
        return False

    @property
    def wet_pct(self) -> float | None:
        """Gelernte Feldkapazität."""
        candidates = self.state["fc_candidates"]
        return round(median(candidates), 1) if candidates else None

    def dry_pct(self, configured: float, wet: float) -> float:
        """Trockenwert: eingestellt, oder niedriger, wenn schon trockener gemessen wurde."""
        min_seen = self.state["min_seen"]
        dry = configured if min_seen is None else min(configured, min_seen)
        return min(dry, wet - MIN_CALIBRATION_GAP_PCT)


class DemandLearner:
    """Lernt aus dem Bodenfeuchtesensor, ob die Zone mehr oder weniger verbraucht als berechnet.

    Gelernt wird nur an trockenen Tagen ohne Regen und Gießen (auch am Vortag nicht,
    damit kein Sickerwasser mitspielt), mit nennenswertem Verbrauch und solange der
    Sensor im aussagekräftigen Bereich liegt. Der Faktor nähert sich langsam dem
    Verhältnis „gemessenes Austrocknen / berechneter Verbrauch“ und wirkt erst nach
    einigen Lerntagen.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        state.setdefault("factor", 1.0)
        state.setdefault("samples", 0)
        state.setdefault("recent", [])

    @property
    def factor(self) -> float:
        """Wirksamer Faktor (1,0, solange zu wenig gelernt wurde)."""
        if self.state["samples"] < LEARN_MIN_SAMPLES:
            return 1.0
        return float(self.state["factor"])

    def learn(
        self,
        day: str,
        observed_mm: float,
        etc_mm: float,
        sensor_start_mm: float,
        sensor_end_mm: float,
        taw_mm: float,
    ) -> float | None:
        """Einen Tag auswerten; gibt das Verhältnis zurück, wenn gelernt wurde."""
        low, high = (taw_mm * f for f in LEARN_VALID_RANGE)
        if etc_mm < LEARN_MIN_ETC_MM:
            return None
        if not (low <= sensor_start_mm <= high and low <= sensor_end_mm <= high):
            return None
        ratio = min(max(observed_mm / etc_mm, LEARN_RATIO_RANGE[0]), LEARN_RATIO_RANGE[1])
        factor = float(self.state["factor"]) * (1 - LEARN_RATE + LEARN_RATE * ratio)
        self.state["factor"] = round(min(max(factor, LEARN_FACTOR_RANGE[0]), LEARN_FACTOR_RANGE[1]), 3)
        self.state["samples"] += 1
        recent: list[dict[str, Any]] = self.state["recent"]
        recent.append({"date": day, "ratio": round(ratio, 2)})
        del recent[:-10]
        return ratio
