"""Bodenwasserhaushalt nach FAO-56 (ohne Home-Assistant-Abhängigkeiten).

Begriffe:
- ET0: Referenzverdunstung (Gras) in mm/Tag.
- ETc: tatsächlicher Bedarf der Zone = ET0 * Kc.
- TAW: total available water, nutzbares Bodenwasser in der Wurzelzone (mm).
- RAW: readily available water = p * TAW. Bis zu dieser Erschöpfung leidet die
  Pflanze nicht; darüber wird gegossen.
- Dr: aktuelle Erschöpfung (Defizit) der Wurzelzone in mm, 0 = Feldkapazität.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

# Nutzbare Feldkapazität in mm pro dm Bodentiefe (Richtwerte nach FAO-56, Tab. 19).
SOIL_AVAILABLE_WATER_MM_PER_DM: dict[str, float] = {
    "sand": 8.0,
    "loamy_sand": 11.0,
    "sandy_loam": 13.0,
    "loam": 15.0,
    "silt_loam": 19.0,
    "clay": 16.0,
}

# Anteil des Wassers, der bei der Bewässerungsart tatsächlich im Boden ankommt.
IRRIGATION_EFFICIENCY: dict[str, float] = {
    "sprinkler": 0.75,
    "drip": 0.9,
}

# Niederschlag, der an Blättern/Oberfläche hängen bleibt und direkt verdunstet (mm/Tag).
RAIN_INTERCEPTION_MM = 0.5
# Davon abgesehen kommt nur dieser Anteil in der Wurzelzone an (Abfluss, Verdunstung).
RAIN_EFFECTIVE_FACTOR = 0.9


@dataclass(frozen=True)
class ZoneParams:
    """Feste Eigenschaften einer Zone."""

    area_m2: float
    throughput_lpm: float
    kc: float = 0.8
    soil_type: str = "loam"
    root_depth_cm: float = 20.0
    depletion_fraction: float = 0.5
    irrigation_type: str = "sprinkler"
    min_duration_min: float = 3.0
    max_duration_min: float = 30.0

    @property
    def taw_mm(self) -> float:
        """Nutzbares Bodenwasser der Wurzelzone."""
        per_dm = SOIL_AVAILABLE_WATER_MM_PER_DM.get(self.soil_type, 15.0)
        return per_dm * self.root_depth_cm / 10.0

    @property
    def raw_mm(self) -> float:
        """Leicht verfügbares Wasser: Bewässerungsschwelle."""
        return self.depletion_fraction * self.taw_mm

    @property
    def efficiency(self) -> float:
        """Wirkungsgrad der Bewässerungsart."""
        return IRRIGATION_EFFICIENCY.get(self.irrigation_type, 0.75)

    @property
    def application_rate_mm_per_min(self) -> float:
        """Im Boden ankommende Wassermenge pro Minute (1 l/m² = 1 mm)."""
        return self.throughput_lpm / self.area_m2 * self.efficiency


def effective_rain(rain_mm: float) -> float:
    """Anteil eines Tagesniederschlags, der die Wurzelzone erreicht."""
    return max(0.0, rain_mm - RAIN_INTERCEPTION_MM) * RAIN_EFFECTIVE_FACTOR


def liters_to_mm(params: ZoneParams, liters: float) -> float:
    """Wirksame Bewässerungshöhe einer Literzahl in der Zone."""
    return liters / params.area_m2 * params.efficiency


def clamp_depletion(params: ZoneParams, depletion: float) -> float:
    """Erschöpfung auf den physikalisch möglichen Bereich begrenzen."""
    return min(max(depletion, 0.0), params.taw_mm)


@dataclass(frozen=True)
class DayResult:
    """Ergebnis eines abgeschlossenen Tages."""

    depletion_mm: float
    etc_mm: float
    effective_rain_mm: float
    percolation_mm: float


def close_day(params: ZoneParams, depletion_mm: float, et0_mm: float, rain_mm: float) -> DayResult:
    """Tagesbilanz anwenden.

    Bewässerungen werden sofort beim Lauf verbucht (apply_irrigation), deshalb
    enthält der Tagesabschluss nur Verdunstung und Regen.
    """
    etc = max(et0_mm, 0.0) * params.kc
    eff_rain = effective_rain(rain_mm)
    raw_depletion = depletion_mm + etc - eff_rain
    percolation = max(0.0, -raw_depletion)
    return DayResult(
        depletion_mm=clamp_depletion(params, raw_depletion),
        etc_mm=etc,
        effective_rain_mm=eff_rain,
        percolation_mm=percolation,
    )


def apply_irrigation(params: ZoneParams, depletion_mm: float, liters: float) -> float:
    """Erschöpfung nach einem Lauf mit der gegebenen Literzahl."""
    return clamp_depletion(params, depletion_mm - liters_to_mm(params, liters))


def depletion_from_soil_moisture(
    params: ZoneParams, moisture_pct: float, dry_pct: float, wet_pct: float
) -> float | None:
    """Erschöpfung aus einem Bodenfeuchtesensor ableiten.

    wet_pct entspricht Feldkapazität (Dr = 0), dry_pct dem Welkepunkt (Dr = TAW).
    """
    if wet_pct <= dry_pct:
        return None
    fraction = (wet_pct - moisture_pct) / (wet_pct - dry_pct)
    return clamp_depletion(params, fraction * params.taw_mm)


def blend_depletion(model_mm: float, sensor_mm: float | None, weight: float) -> float:
    """Modell und Sensor gewichtet zusammenführen (weight = Anteil des Sensors)."""
    if sensor_mm is None:
        return model_mm
    weight = min(max(weight, 0.0), 1.0)
    return model_mm * (1.0 - weight) + sensor_mm * weight


@dataclass(frozen=True)
class Conditions:
    """Wetterlage zum Zeitpunkt der Empfehlung."""

    rain_forecast_24h_mm: float = 0.0
    min_temp_24h_c: float | None = None
    wind_kmh: float | None = None
    raining_now: bool = False


@dataclass(frozen=True)
class Thresholds:
    """Sperrschwellen aus den Optionen."""

    rain_skip_mm: float = 3.0
    frost_c: float = 4.0
    wind_kmh: float = 30.0


@dataclass
class Recommendation:
    """Gießempfehlung für eine Zone."""

    water: bool
    target_mm: float
    minutes: int
    liters: int
    reason: str
    blocked_by: list[str] = field(default_factory=list)
    capped: bool = False


def _fmt(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def recommend(
    params: ZoneParams,
    depletion_mm: float,
    conditions: Conditions,
    thresholds: Thresholds,
) -> Recommendation:
    """Entscheiden, ob und wie lange heute gegossen werden sollte."""
    blocked: list[str] = []
    if conditions.raining_now:
        blocked.append("Regensensor meldet Regen")
    if conditions.rain_forecast_24h_mm >= thresholds.rain_skip_mm:
        blocked.append(f"{_fmt(conditions.rain_forecast_24h_mm)} mm Regen angesagt")
    if conditions.min_temp_24h_c is not None and conditions.min_temp_24h_c < thresholds.frost_c:
        blocked.append(f"Frostgefahr ({_fmt(conditions.min_temp_24h_c)} °C)")
    if conditions.wind_kmh is not None and conditions.wind_kmh > thresholds.wind_kmh:
        blocked.append(f"Wind {conditions.wind_kmh:.0f} km/h")

    deficit = f"Defizit {_fmt(depletion_mm)} mm (Schwelle {_fmt(params.raw_mm)} mm)"

    if depletion_mm < params.raw_mm:
        return Recommendation(False, 0.0, 0, 0, f"{deficit}: Boden hat genug Wasser", blocked)

    if blocked:
        return Recommendation(False, 0.0, 0, 0, f"{deficit}, aber gesperrt: {', '.join(blocked)}", blocked)

    # Erwarteten Regen anrechnen und zurück auf Feldkapazität auffüllen.
    rain_credit = effective_rain(conditions.rain_forecast_24h_mm)
    target = max(depletion_mm - rain_credit, 0.0)
    minutes_exact = target / params.application_rate_mm_per_min
    capped = minutes_exact > params.max_duration_min
    minutes = math.ceil(min(minutes_exact, params.max_duration_min))
    if minutes < params.min_duration_min:
        return Recommendation(
            False,
            target,
            0,
            0,
            f"{deficit}, nur {minutes} min nötig (unter Minimum {params.min_duration_min:.0f} min)",
            blocked,
        )

    liters = round(minutes * params.throughput_lpm)
    reason = f"{deficit}"
    if rain_credit > 0:
        reason += f", {_fmt(rain_credit)} mm Regen angerechnet"
    reason += f" → {minutes} min / {liters} l"
    if capped:
        reason += f" (auf {params.max_duration_min:.0f} min gekappt)"
    return Recommendation(True, target, minutes, liters, reason, blocked, capped)
