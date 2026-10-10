"""Coordinator: Wetter holen, Tage abschließen, laufenden Wasserbedarf berechnen."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import logging
from statistics import median
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import model, plants
from .const import (
    CONF_AREA,
    CONF_COMPARE_ENTITY,
    CONF_DEPLETION_FRACTION,
    CONF_FLOW_SENSOR,
    CONF_FROST_C,
    CONF_IRRIGATION_TYPE,
    CONF_KC,
    CONF_KC_FACTOR,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_PLANT,
    CONF_RAIN_PROBABILITY,
    CONF_RAIN_SENSOR,
    CONF_RAIN_SKIP_MM,
    CONF_ROOT_DEPTH,
    CONF_SOIL_DRY_PCT,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_SOIL_SENSOR_WEIGHT,
    CONF_SOIL_TYPE,
    CONF_SOIL_WET_PCT,
    CONF_THROUGHPUT,
    CONF_VALVE,
    CONF_VOLUME_SENSOR,
    CONF_WATER_PRICE,
    CONF_WIND_KMH,
    DEFAULT_FROST_C,
    DEFAULT_RAIN_PROBABILITY,
    DEFAULT_RAIN_SKIP_MM,
    DEFAULT_SOIL_DRY_PCT,
    DEFAULT_SOIL_SENSOR_WEIGHT,
    DEFAULT_SOIL_WET_PCT,
    DEFAULT_WATER_PRICE,
    DEFAULT_WIND_KMH,
    DOMAIN,
    ENSEMBLE_MAX_AGE,
    HISTORY_DAYS,
    LIVE_INTERVAL,
    REFRESH_COOLDOWN_SECONDS,
    STATUS_PREPARE_SPRING,
    STATUS_RESTING,
    STATUS_SEASON,
    STATUS_WINTERIZE,
    STATUS_WINTERIZED,
    STORAGE_VERSION,
    SUBENTRY_ZONE,
    THROUGHPUT_DEVIATION,
    THROUGHPUT_SAMPLES,
    UPDATE_INTERVAL,
    WINTER_FROST_C,
)
from .irrigation_tracker import INVALID_STATES, IrrigationTracker, RunRecord, read_float
from .learning import DemandLearner, SoilCalibrator
from .open_meteo import (
    EnsembleRain,
    OpenMeteoError,
    WeatherData,
    async_fetch,
    async_fetch_daily_means,
    async_fetch_ensemble,
)

_LOGGER = logging.getLogger(__name__)

RATE_UNITS = ("mm/h", "in/h", "mm/d")
# Zustände von Regenmeldern ohne Menge (z. B. Zigbee „Rainwater“: raining / none)
RAINING_STATES = {"on", "true", "raining", "rain", "rainy", "wet", "detected", "yes"}
ARCHIVE_DELAY_DAYS = 6
DURATION_UNITS = {"s": 1 / 60, "min": 1.0, "h": 60.0}


@dataclass
class ZoneSnapshot:
    """Berechneter Zustand einer Zone für die Entities."""

    depletion_mm: float
    committed_depletion_mm: float
    soil_water_pct: float
    demand_liters: float
    taw_mm: float
    raw_mm: float
    plant: str
    kc: float
    phase: str | None
    phase_source: str
    spring_gts: float | None
    etc_so_far_mm: float
    etc_today_mm: float
    etc_tomorrow_mm: float | None
    etc_7d_mm: float
    recommendation: model.Recommendation
    sensor_depletion_mm: float | None
    soil_moisture_pct: float | None
    measured_throughput_lpm: float | None
    current_flow_lpm: float | None
    live_run_liters: float
    faults: list[str]
    compare_minutes: float | None
    last_run: dict[str, Any] | None
    total_liters: float
    watering: bool
    history: list[dict[str, Any]]
    soil_wet_pct: float | None = None
    soil_dry_pct: float | None = None
    soil_calibration: str | None = None
    learned_factor: float = 1.0
    learned_samples: int = 0
    learned_recent: list[dict[str, Any]] = field(default_factory=list)
    area_m2: float = 0.0
    throughput_lpm: float = 0.0
    et0_today_mm: float | None = None
    last_calculated: str | None = None
    data_points: int = 0


@dataclass
class Snapshot:
    """Gesamter berechneter Zustand."""

    et0_so_far_mm: float = 0.0
    et0_today_mm: float | None = None
    et0_yesterday_mm: float | None = None
    rain_today_mm: float | None = None
    rain_yesterday_mm: float | None = None
    rain_source: str = "open_meteo"
    rain_forecast_24h_mm: float = 0.0
    min_temp_24h_c: float | None = None
    wind_kmh: float | None = None
    raining_now: bool = False
    frost: bool = False
    last_closed: date | None = None
    gts: float | None = None
    rain_probability_pct: float | None = None
    rain_expected_24h_mm: float | None = None
    rain_blocked: bool = False
    season_status: str = STATUS_SEASON
    season_checklist: list[str] = field(default_factory=list)
    weekly: dict[str, Any] = field(default_factory=dict)
    block_reason: str = ""
    flow_report: str = ""
    last_run_report: str = ""
    weather_updated: datetime | None = None
    zones: dict[str, ZoneSnapshot] = field(default_factory=dict)


class Zone:
    """Laufzeitobjekt einer Zone (Subentry)."""

    def __init__(self, subentry: ConfigSubentry, state: dict[str, Any]) -> None:
        self.subentry_id = subentry.subentry_id
        self.name = subentry.title
        self.data = dict(subentry.data)
        self.plant = self.data.get(CONF_PLANT, plants.PLANT_CUSTOM)
        profile = plants.PLANTS.get(self.plant)
        self.params = model.ZoneParams(
            area_m2=float(self.data[CONF_AREA]),
            throughput_lpm=float(self.data[CONF_THROUGHPUT]),
            kc=float(self.data.get(CONF_KC, 0.8)),
            soil_type=self.data.get(CONF_SOIL_TYPE, "loam"),
            root_depth_cm=float(self.data.get(CONF_ROOT_DEPTH) or (profile.root_depth_cm if profile else 20)),
            depletion_fraction=float(
                self.data.get(CONF_DEPLETION_FRACTION) or (profile.depletion_fraction if profile else 0.5)
            ),
            irrigation_type=self.data.get(CONF_IRRIGATION_TYPE, "sprinkler"),
            min_duration_min=float(self.data.get(CONF_MIN_DURATION, 3)),
            max_duration_min=float(self.data.get(CONF_MAX_DURATION, 30)),
        )
        self.state = state
        state.setdefault("depletion", 0.0)
        state.setdefault("total_liters", 0.0)
        state.setdefault("last_run", None)
        state.setdefault("throughput_samples", [])
        state.setdefault("history", [])
        state.setdefault("irrigation_mm_by_date", {})
        state.setdefault("liters_by_date", {})
        state.setdefault("runs_by_date", {})
        self.calibrator = SoilCalibrator(state.setdefault("soil_calibration", {}))
        self.learner = DemandLearner(state.setdefault("demand_learning", {}))
        self.tracker: IrrigationTracker | None = None
        # Tagesmitteltemperaturen, vom Coordinator geteilt und laufend ergänzt.
        self.daily_means: dict[date, float] = {}

    def season(self, day: date) -> tuple[float, str | None, str]:
        """(Kc, Phase, Quelle) des Tages nach Jahreszeit und tatsächlichem Wetter."""
        if self.plant == plants.PLANT_CUSTOM:
            return round(self.params.kc * self.learner.factor, 3), None, plants.SOURCE_CALENDAR
        dormant = plants.weather_dormant(self.plant, self.daily_means, day)
        kc, phase, source = plants.adjust_for_weather(self.plant, day, dormant)
        factor = float(self.data.get(CONF_KC_FACTOR, 1.0)) * self.learner.factor
        return round(kc * factor, 3), phase, source

    def kc_for(self, day: date) -> float:
        """Pflanzenfaktor des Tages."""
        return self.season(day)[0]

    def params_for(self, day: date) -> model.ZoneParams:
        """Zonenparameter mit dem Pflanzenfaktor des Tages."""
        return replace(self.params, kc=self.kc_for(day))

    @property
    def depletion(self) -> float:
        return float(self.state["depletion"])

    @depletion.setter
    def depletion(self, value: float) -> None:
        # Stand zu Tagesbeginn. Darf negativ werden, wenn heute schon gegossen oder
        # gesetzt wurde: der laufende Verbrauch des Tages wird darauf addiert.
        taw = self.params.taw_mm
        self.state["depletion"] = round(min(max(value, -taw), taw), 3)

    @property
    def soil_calibration(self) -> str:
        """Woher die Kalibrierwerte stammen: manual, learned oder configured."""
        if "soil_wet_pct" in self.state:
            return "manual"
        if self.calibrator.wet_pct is not None:
            return "learned"
        return "configured"

    @property
    def soil_wet_pct(self) -> float:
        """Feldkapazität: von Hand kalibriert, sonst gelernt, sonst eingestellt."""
        if "soil_wet_pct" in self.state:
            return float(self.state["soil_wet_pct"])
        learned = self.calibrator.wet_pct
        if learned is not None:
            return learned
        return float(self.data.get(CONF_SOIL_WET_PCT, DEFAULT_SOIL_WET_PCT))

    @property
    def soil_dry_pct(self) -> float:
        """Trockenwert: von Hand kalibriert, sonst eingestellt (oder niedriger, wenn schon gemessen)."""
        if "soil_dry_pct" in self.state:
            return float(self.state["soil_dry_pct"])
        configured = float(self.data.get(CONF_SOIL_DRY_PCT, DEFAULT_SOIL_DRY_PCT))
        return self.calibrator.dry_pct(configured, self.soil_wet_pct)


def _de(value: float, digits: int = 1) -> str:
    """Zahl mit deutschem Dezimalkomma."""
    return f"{value:.{digits}f}".replace(".", ",")


class SmarteBewaesserungCoordinator(DataUpdateCoordinator[Snapshot]):
    """Hält Wetter, Wasserkonten und Empfehlungen aller Zonen.

    Wetter wird stündlich geladen; der laufende Wasserbedarf wird zusätzlich alle
    fünf Minuten und bei jeder Änderung an Ventil, Durchfluss oder Sensoren neu
    berechnet.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._stored: dict[str, Any] = {}
        self.weather: WeatherData | None = None
        self.weather_updated: datetime | None = None
        self.zones: dict[str, Zone] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._rain_last: float | None = None
        self._means: dict[date, float] = {}
        self.ensemble: EnsembleRain | None = None
        self.ensemble_updated: datetime | None = None
        self._debouncer = Debouncer(
            hass, _LOGGER, cooldown=REFRESH_COOLDOWN_SECONDS, immediate=True, function=self.refresh_now
        )

    # ------------------------------------------------------------------ Optionen
    def _opt(self, key: str, default: Any) -> Any:
        entry = self.config_entry
        return entry.options.get(key, entry.data.get(key, default))

    @property
    def thresholds(self) -> model.Thresholds:
        return model.Thresholds(
            rain_skip_mm=float(self._opt(CONF_RAIN_SKIP_MM, DEFAULT_RAIN_SKIP_MM)),
            frost_c=float(self._opt(CONF_FROST_C, DEFAULT_FROST_C)),
            wind_kmh=float(self._opt(CONF_WIND_KMH, DEFAULT_WIND_KMH)),
            rain_probability=float(self._opt(CONF_RAIN_PROBABILITY, DEFAULT_RAIN_PROBABILITY)) / 100,
        )

    @property
    def water_price(self) -> float:
        return float(self._opt(CONF_WATER_PRICE, DEFAULT_WATER_PRICE))

    @property
    def winterized(self) -> bool:
        return bool(self._stored.get("winterized", False))

    @property
    def rain_sensor(self) -> str | None:
        return self._opt(CONF_RAIN_SENSOR, None) or None

    @property
    def soil_sensor_weight(self) -> float:
        return float(self._opt(CONF_SOIL_SENSOR_WEIGHT, DEFAULT_SOIL_SENSOR_WEIGHT))

    # ------------------------------------------------------------------ Setup
    async def async_setup(self) -> None:
        """Gespeicherten Zustand laden, Zonen anlegen, Listener registrieren."""
        self._stored = await self._store.async_load() or {}
        self._stored.setdefault("zones", {})
        self._stored.setdefault("rain_by_date", {})
        self._stored.setdefault("daily_temps", {})
        self._means.update({date.fromisoformat(k): float(v) for k, v in self._stored["daily_temps"].items()})
        if "last_closed" not in self._stored:
            # Neu eingerichtet: nicht rückwirkend rechnen, ab heute bilanzieren.
            self._stored["last_closed"] = (dt_util.now().date() - timedelta(days=1)).isoformat()

        for subentry in self.config_entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_ZONE:
                continue
            state = self._stored["zones"].setdefault(subentry.subentry_id, {})
            zone = Zone(subentry, state)
            zone.daily_means = self._means
            self.zones[subentry.subentry_id] = zone
            valve = zone.data.get(CONF_VALVE)
            flow = zone.data.get(CONF_FLOW_SENSOR)
            if valve or flow:
                zone.tracker = IrrigationTracker(
                    self.hass,
                    valve_entity=valve,
                    volume_sensor=zone.data.get(CONF_VOLUME_SENSOR),
                    flow_sensor=flow,
                    throughput_lpm=zone.params.throughput_lpm,
                    max_duration_min=zone.params.max_duration_min,
                    on_run=lambda run, z=zone: self._handle_run(z, run),
                    on_change=self.refresh_snapshot,
                )
                zone.tracker.async_start()

        # Gespeicherte Zustände gelöschter Zonen entfernen.
        for sub_id in list(self._stored["zones"]):
            if sub_id not in self.zones:
                del self._stored["zones"][sub_id]

        watched = [
            e
            for z in self.zones.values()
            for e in (z.data.get(CONF_SOIL_MOISTURE_SENSOR), z.data.get(CONF_COMPARE_ENTITY))
            if e
        ]
        if self.rain_sensor:
            watched.append(self.rain_sensor)
            self._rain_last = self._rain_reading()
        if watched:
            self._unsubs.append(async_track_state_change_event(self.hass, watched, self._watched_changed))
        # Kurz nach Mitternacht den Vortag mit vollständigen Daten abschließen.
        self._unsubs.append(async_track_time_change(self.hass, self._midnight, hour=0, minute=5, second=0))
        # Laufender Bedarf zwischen den Wetterabrufen.
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, LIVE_INTERVAL))

    @callback
    def async_shutdown_listeners(self) -> None:
        """Listener und Tracker beenden."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._debouncer.async_shutdown()
        for zone in self.zones.values():
            if zone.tracker:
                zone.tracker.async_stop()

    async def _midnight(self, _now: datetime) -> None:
        await self.async_request_refresh()

    @callback
    def _tick(self, _now: datetime) -> None:
        self._feed_soil()
        self.refresh_now()

    @callback
    def _feed_soil(self) -> None:
        """Bodenfeuchte an die Selbstkalibrierung geben."""
        ts = dt_util.utcnow().timestamp()
        for zone in self.zones.values():
            moisture = read_float(self.hass, zone.data.get(CONF_SOIL_MOISTURE_SENSOR))
            if moisture is not None and zone.calibrator.feed(ts, moisture):
                _LOGGER.info("%s: Feldkapazität gelernt, jetzt %s %%", zone.name, zone.calibrator.wet_pct)
                self._save()

    # ------------------------------------------------------------------ Regensensor
    def _rain_sensor_kind(self) -> str | None:
        """'binary', 'rate' (mm/h) oder 'amount' (mm-Zähler)."""
        entity = self.rain_sensor
        if not entity:
            return None
        if entity.startswith("binary_sensor."):
            return "binary"
        state = self.hass.states.get(entity)
        unit = state.attributes.get("unit_of_measurement", "") if state else ""
        if unit in RATE_UNITS:
            return "rate"
        if not unit and state is not None and read_float(self.hass, entity) is None:
            # Ohne Einheit und nicht numerisch: Regenmelder mit Text-Zustand (regnet / regnet nicht)
            return "binary"
        return "amount"

    def _rain_reading(self) -> float | None:
        if self._rain_sensor_kind() != "amount":
            return None
        return read_float(self.hass, self.rain_sensor)

    @callback
    def _watched_changed(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == self.rain_sensor and self._rain_sensor_kind() == "amount":
            current = self._rain_reading()
            last = self._rain_last
            if current is not None:
                if last is not None:
                    # Zähler gestiegen: Differenz; Zähler zurückgesetzt: neuer Stand ist neuer Regen.
                    delta = current - last if current >= last else current
                    if delta > 0:
                        key = dt_util.now().date().isoformat()
                        rain = self._stored["rain_by_date"]
                        rain[key] = round(rain.get(key, 0.0) + delta, 2)
                        self._save()
                self._rain_last = current
        if any(event.data["entity_id"] == z.data.get(CONF_SOIL_MOISTURE_SENSOR) for z in self.zones.values()):
            self._feed_soil()
        self.refresh_snapshot()

    def _mark_rain_sensor_day(self) -> None:
        """Tag als vom Regensensor abgedeckt markieren (auch ohne Regen = 0 mm)."""
        if self._rain_sensor_kind() == "amount" and self._rain_reading() is not None:
            self._stored["rain_by_date"].setdefault(dt_util.now().date().isoformat(), 0.0)

    def _sensor_rain(self, day: date) -> float | None:
        value = self._stored["rain_by_date"].get(day.isoformat())
        return float(value) if value is not None else None

    # ------------------------------------------------------------------ Bodenfeuchte
    def _soil_reading(self, zone: Zone) -> tuple[float | None, float | None]:
        """(Bodenfeuchte %, daraus abgeleitete Erschöpfung mm)."""
        moisture = read_float(self.hass, zone.data.get(CONF_SOIL_MOISTURE_SENSOR))
        if moisture is None:
            return None, None
        return moisture, model.depletion_from_soil_moisture(zone.params, moisture, zone.soil_dry_pct, zone.soil_wet_pct)

    # ------------------------------------------------------------------ Update
    async def _async_update_data(self) -> Snapshot:
        try:
            self.weather = await async_fetch(
                async_get_clientsession(self.hass),
                self.hass.config.latitude,
                self.hass.config.longitude,
                self.hass.config.time_zone,
            )
            self.weather_updated = dt_util.utcnow()
        except OpenMeteoError as err:
            if self.weather is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.warning("%s – rechne mit den letzten Wetterdaten weiter", err)

        await self._update_ensemble()
        self._store_daily_means()
        await self._backfill_daily_means()
        self._feed_soil()
        self._mark_rain_sensor_day()
        self._close_days()
        self._prune()
        self._save()
        return self._build_snapshot()

    async def _update_ensemble(self) -> None:
        """Regen-Ensemble laden; bei Ausfall gilt die normale Vorhersage."""
        try:
            self.ensemble = await async_fetch_ensemble(
                async_get_clientsession(self.hass),
                self.hass.config.latitude,
                self.hass.config.longitude,
                self.hass.config.time_zone,
            )
            self.ensemble_updated = dt_util.utcnow()
        except OpenMeteoError as err:
            _LOGGER.warning("%s – Regenwahrscheinlichkeit vorerst aus der normalen Vorhersage", err)
            if self.ensemble_updated and dt_util.utcnow() - self.ensemble_updated > ENSEMBLE_MAX_AGE:
                self.ensemble = None

    def _store_daily_means(self) -> None:
        """Tagesmittel abgeschlossener Tage aus der Vorhersage-Antwort übernehmen."""
        assert self.weather is not None
        today = dt_util.now().date()
        for day, weather in self.weather.daily.items():
            if day < today and weather.temp_mean_c is not None:
                self._set_mean(day, weather.temp_mean_c)

    def _set_mean(self, day: date, mean: float) -> None:
        self._means[day] = mean
        self._stored["daily_temps"][day.isoformat()] = round(mean, 2)

    async def _backfill_daily_means(self) -> None:
        """Fehlende Tagesmittel seit 1. Januar einmal am Tag aus dem Archiv holen."""
        today = dt_util.now().date()
        if self._stored.get("temps_backfilled") == today.isoformat():
            return
        # Das Archiv hat einige Tage Verzug; die letzten Tage kommen aus der Vorhersage.
        end = today - timedelta(days=ARCHIVE_DELAY_DAYS)
        missing = [
            d
            for d in (
                date(today.year, 1, 1) + timedelta(days=i) for i in range((end - date(today.year, 1, 1)).days + 1)
            )
            if d not in self._means
        ]
        if missing:
            try:
                means = await async_fetch_daily_means(
                    async_get_clientsession(self.hass),
                    self.hass.config.latitude,
                    self.hass.config.longitude,
                    self.hass.config.time_zone,
                    min(missing),
                    max(missing),
                )
            except OpenMeteoError as err:
                _LOGGER.warning("%s – Saisonphasen richten sich vorerst nach dem Kalender", err)
                return
            for day, mean in means.items():
                self._set_mean(day, mean)
        self._stored["temps_backfilled"] = today.isoformat()

    def _close_days(self) -> None:
        """Alle vollständig vergangenen, noch offenen Tage bilanzieren."""
        assert self.weather is not None
        today = dt_util.now().date()
        last_closed = date.fromisoformat(self._stored["last_closed"])
        day = last_closed + timedelta(days=1)
        while day < today:
            totals = self.weather.day_totals(day)
            if totals is None:
                _LOGGER.warning("Keine Wetterdaten für %s, Tag wird übersprungen", day)
                day += timedelta(days=1)
                continue
            et0, rain = totals
            sensor_rain = self._sensor_rain(day)
            source = "open_meteo"
            if sensor_rain is not None:
                rain, source = sensor_rain, "rain_sensor"
            is_yesterday = day == today - timedelta(days=1)
            for zone in self.zones.values():
                params = zone.params_for(day)
                result = model.close_day(params, zone.depletion, et0, rain)
                moisture, sensor_depletion = self._soil_reading(zone) if is_yesterday else (None, None)
                if sensor_depletion is not None:
                    self._learn_demand(zone, day, result, sensor_depletion)
                zone.depletion = model.blend_depletion(result.depletion_mm, sensor_depletion, self.soil_sensor_weight)
                zone.state["history"].append(
                    {
                        "date": day.isoformat(),
                        "kc": params.kc,
                        "et0": round(et0, 2),
                        "etc": round(result.etc_mm, 2),
                        "rain": round(rain, 2),
                        "rain_source": source,
                        "effective_rain": round(result.effective_rain_mm, 2),
                        "irrigation_mm": round(zone.state["irrigation_mm_by_date"].get(day.isoformat(), 0.0), 2),
                        "percolation": round(result.percolation_mm, 2),
                        "soil_moisture": moisture,
                        "depletion": round(zone.depletion, 2),
                    }
                )
            self._stored["last_closed"] = day.isoformat()
            day += timedelta(days=1)

    def _learn_demand(self, zone: Zone, day: date, result: model.DayResult, sensor_end: float) -> None:
        """Gemessenes Austrocknen eines trockenen Tages mit dem berechneten Verbrauch vergleichen."""
        previous = zone.state.get("soil_close")
        zone.state["soil_close"] = {"date": day.isoformat(), "depletion": round(sensor_end, 2)}
        if previous is None or previous["date"] != (day - timedelta(days=1)).isoformat():
            return
        if zone.soil_calibration == "configured":
            return  # Ohne Kalibrierung stimmt die Größenordnung des Sensors nicht.
        irrigated = zone.state["irrigation_mm_by_date"]
        yesterday = (day - timedelta(days=1)).isoformat()
        prior = next((h for h in zone.state["history"] if h["date"] == yesterday), None)
        if (
            result.effective_rain_mm > 0
            or irrigated.get(day.isoformat(), 0) > 0
            or irrigated.get(yesterday, 0) > 0
            or (prior is not None and prior["effective_rain"] > 0)
        ):
            return
        ratio = zone.learner.learn(
            day.isoformat(),
            observed_mm=sensor_end - previous["depletion"],
            etc_mm=result.etc_mm,
            sensor_start_mm=previous["depletion"],
            sensor_end_mm=sensor_end,
            taw_mm=zone.params.taw_mm,
        )
        if ratio is not None:
            _LOGGER.info(
                "%s: Verbrauch %.0f %% der Berechnung, Faktor jetzt %s",
                zone.name,
                ratio * 100,
                zone.learner.state["factor"],
            )

    def _prune(self) -> None:
        cutoff = (dt_util.now().date() - timedelta(days=HISTORY_DAYS)).isoformat()
        # Temperaturen werden nur für das laufende Jahr gebraucht (GTS ab 1. Januar, Herbst ab August).
        year_start = date(dt_util.now().year, 1, 1)
        for day in [d for d in self._means if d < year_start]:
            del self._means[day]
            self._stored["daily_temps"].pop(day.isoformat(), None)
        rain = self._stored["rain_by_date"]
        for key in [k for k in rain if k < cutoff]:
            del rain[key]
        for zone in self.zones.values():
            zone.state["history"] = [h for h in zone.state["history"] if h["date"] >= cutoff]
            for name in ("irrigation_mm_by_date", "liters_by_date", "runs_by_date"):
                by_date = zone.state[name]
                for key in [k for k in by_date if k < cutoff]:
                    del by_date[key]

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._stored, 5)

    @callback
    def refresh_snapshot(self) -> None:
        """Gedrosselt neu rechnen (für häufige Ereignisse wie Durchflusswerte)."""
        self._debouncer.async_schedule_call()

    @callback
    def refresh_now(self) -> None:
        """Ohne neuen Wetterabruf sofort neu rechnen und Entities aktualisieren."""
        if self.weather is None:
            return
        self.async_set_updated_data(self._build_snapshot())

    # ------------------------------------------------------------------ Läufe
    @callback
    def _handle_run(self, zone: Zone, run: RunRecord) -> None:
        self.record_run(zone, run)
        self.refresh_now()

    def record_run(self, zone: Zone, run: RunRecord) -> None:
        """Lauf verbuchen: Wasserkonto, Statistik, Durchsatz."""
        key = dt_util.as_local(run.start).date().isoformat()
        if key == dt_util.now().date().isoformat():
            # Gegen den aktuellen Stand rechnen: Wasser über Feldkapazität versickert.
            self._set_live_depletion(zone, self._live_depletion(zone) - model.liters_to_mm(zone.params, run.liters))
        else:
            zone.depletion = model.apply_irrigation(zone.params, zone.depletion, run.liters)
        by_date = zone.state["irrigation_mm_by_date"]
        by_date[key] = round(by_date.get(key, 0.0) + model.liters_to_mm(zone.params, run.liters), 3)
        zone.state["total_liters"] = round(zone.state["total_liters"] + run.liters, 1)
        liters = zone.state["liters_by_date"]
        liters[key] = round(liters.get(key, 0.0) + run.liters, 1)
        runs = zone.state["runs_by_date"]
        runs[key] = runs.get(key, 0) + 1
        zone.state["last_run"] = run.as_dict()
        if run.measured and run.minutes >= 1 and run.liters > 0:
            samples = zone.state["throughput_samples"]
            samples.append(round(run.liters / run.minutes, 2))
            del samples[:-THROUGHPUT_SAMPLES]
        self._save()

    def set_depletion(self, zone: Zone, depletion_mm: float) -> None:
        """Wasserkonto von Hand auf den heutigen Stand setzen."""
        self._set_live_depletion(zone, depletion_mm)
        self._save()
        self.refresh_now()

    def set_winterized(self, winterized: bool) -> None:
        """Anlage als winterfest markieren (sperrt Empfehlungen) oder wieder freigeben."""
        self._stored["winterized"] = winterized
        self._save()
        self.refresh_now()

    def calibrate_soil(self, zone: Zone, point: str) -> float:
        """Aktuellen Bodenfeuchtewert als trocken/nass speichern."""
        moisture, _ = self._soil_reading(zone)
        if moisture is None:
            raise ValueError("Bodenfeuchtesensor liefert keinen Wert")
        zone.state["soil_dry_pct" if point == "dry" else "soil_wet_pct"] = moisture
        self._save()
        self.refresh_now()
        return moisture

    # ------------------------------------------------------------------ Laufender Tag
    def _rain_today(self) -> tuple[float, str]:
        sensor_rain = self._sensor_rain(dt_util.now().date())
        if sensor_rain is not None:
            return sensor_rain, "rain_sensor"
        assert self.weather is not None
        return self.weather.so_far_today(dt_util.now())[1], "open_meteo"

    def _live_depletion(self, zone: Zone) -> float:
        """Aktuelle Erschöpfung: Stand zu Tagesbeginn plus Verbrauch minus Regen seit Mitternacht."""
        etc_so_far, eff_rain_so_far = self._today_so_far(zone)
        return model.clamp_depletion(zone.params, zone.depletion + etc_so_far - eff_rain_so_far)

    def _set_live_depletion(self, zone: Zone, value: float) -> None:
        """Aktuelle Erschöpfung setzen, indem der Stand zu Tagesbeginn angepasst wird."""
        etc_so_far, eff_rain_so_far = self._today_so_far(zone)
        zone.depletion = model.clamp_depletion(zone.params, value) - etc_so_far + eff_rain_so_far

    def _today_so_far(self, zone: Zone) -> tuple[float, float]:
        """(ETc bisher heute, wirksamer Regen bisher heute) in mm."""
        if self.weather is None:
            return 0.0, 0.0
        now = dt_util.now()
        et0, _ = self.weather.so_far_today(now)
        rain, _ = self._rain_today()
        return et0 * zone.kc_for(now.date()), model.effective_rain(rain)

    # ------------------------------------------------------------------ Snapshot
    def _conditions(self) -> model.Conditions:
        assert self.weather is not None
        now = dt_util.now()
        upcoming = self.weather.next_hours(now, 24)
        temps = [h.temp_c for h in upcoming if h.temp_c is not None]
        current = self.weather.current_hour(now)
        raining = False
        kind = self._rain_sensor_kind()
        if kind == "binary":
            state = self.hass.states.get(self.rain_sensor)
            raining = state is not None and str(state.state).lower() in RAINING_STATES
        elif kind == "rate":
            raining = (read_float(self.hass, self.rain_sensor) or 0.0) > 0
        probability = expected_effective = None
        if self.ensemble is not None:
            sums = self.ensemble.member_sums(now, 24)
            if sums:
                threshold = self.thresholds.rain_skip_mm
                probability = sum(1 for x in sums if x >= threshold) / len(sums)
                expected_effective = sum(model.effective_rain(x) for x in sums) / len(sums)
        return model.Conditions(
            rain_forecast_24h_mm=round(self.weather.upcoming(now, 24)[1], 1),
            min_temp_24h_c=min(temps) if temps else None,
            wind_kmh=current.wind_kmh if current else None,
            raining_now=raining,
            rain_probability=probability,
            rain_expected_effective_mm=expected_effective,
            winterized=self.winterized,
        )

    def _compare_minutes(self, zone: Zone) -> float | None:
        entity = zone.data.get(CONF_COMPARE_ENTITY)
        if not entity:
            return None
        state = self.hass.states.get(entity)
        if state is None or state.state in INVALID_STATES:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement", "s")
        return round(value * DURATION_UNITS.get(unit, 1 / 60), 1)

    def _faults(self, zone: Zone, measured_throughput: float | None) -> list[str]:
        faults: list[str] = []
        tracker = zone.tracker
        if tracker is not None:
            if not tracker.valve_available:
                faults.append("Ventil nicht erreichbar")
            if tracker.stuck_open:
                faults.append("Ventil länger offen als maximale Laufzeit")
        for key, label in (
            (CONF_VOLUME_SENSOR, "Mengenzähler"),
            (CONF_FLOW_SENSOR, "Volumenstrom-Sensor"),
            (CONF_SOIL_MOISTURE_SENSOR, "Bodenfeuchtesensor"),
        ):
            entity = zone.data.get(key)
            if entity:
                state = self.hass.states.get(entity)
                if state is None or state.state in INVALID_STATES:
                    faults.append(f"{label} nicht verfügbar")
        last = zone.state.get("last_run")
        if last:
            if last.get("no_flow"):
                faults.append("Letzter Lauf: Ventil offen, aber kein Durchfluss")
            if last.get("implausible_volume"):
                faults.append("Letzter Lauf: gemessene Menge unplausibel, geschätzt")
        if measured_throughput is not None and len(zone.state["throughput_samples"]) >= 2:
            configured = zone.params.throughput_lpm
            deviation = abs(measured_throughput - configured) / configured
            if deviation > THROUGHPUT_DEVIATION:
                faults.append(f"Durchsatz gemessen {measured_throughput:.1f} l/min statt {configured:.1f} l/min")
        return faults

    def _build_snapshot(self) -> Snapshot:
        assert self.weather is not None
        now = dt_util.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)
        conditions = self._conditions()
        thresholds = self.thresholds
        et0_so_far, _ = self.weather.so_far_today(now)
        et0_rest_of_day = self.weather.upcoming(now, (24 - now.hour - now.minute / 60))[0]
        rain_today, source = self._rain_today()
        yesterday_totals = self.weather.day_totals(yesterday)
        rain_yesterday = self._sensor_rain(yesterday)
        if rain_yesterday is None and yesterday_totals:
            rain_yesterday = yesterday_totals[1]
        tomorrow_totals = self.weather.day_totals(tomorrow)

        snap = Snapshot(
            et0_so_far_mm=round(et0_so_far, 2),
            et0_today_mm=round(et0_so_far + et0_rest_of_day, 2),
            et0_yesterday_mm=round(yesterday_totals[0], 2) if yesterday_totals else None,
            rain_today_mm=round(rain_today, 1),
            rain_yesterday_mm=round(rain_yesterday, 1) if rain_yesterday is not None else None,
            rain_source=source,
            rain_forecast_24h_mm=conditions.rain_forecast_24h_mm,
            min_temp_24h_c=conditions.min_temp_24h_c,
            wind_kmh=conditions.wind_kmh,
            raining_now=conditions.raining_now,
            frost=conditions.min_temp_24h_c is not None and conditions.min_temp_24h_c < thresholds.frost_c,
            last_closed=date.fromisoformat(self._stored["last_closed"]),
            gts=plants.grassland_temperature_sum(self._means, today),
            rain_probability_pct=round(conditions.rain_probability * 100)
            if conditions.rain_probability is not None
            else None,
            rain_expected_24h_mm=self._ensemble_expected(now),
            rain_blocked=conditions.raining_now
            or (
                conditions.rain_probability >= thresholds.rain_probability
                if conditions.rain_probability is not None
                else conditions.rain_forecast_24h_mm >= thresholds.rain_skip_mm
            ),
            weather_updated=self.weather_updated,
        )
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        data_points = sum(1 for h in self.weather.hourly if midnight < h.time <= now.replace(tzinfo=None))
        for sub_id, zone in self.zones.items():
            kc, phase, phase_source = zone.season(today)
            params = replace(zone.params, kc=kc)
            etc_so_far, eff_rain_so_far = self._today_so_far(zone)
            tracker = zone.tracker
            running = bool(tracker and tracker.running)
            live_liters = tracker.live_liters if tracker and running else 0.0
            depletion = model.clamp_depletion(
                params,
                zone.depletion + etc_so_far - eff_rain_so_far - model.liters_to_mm(params, live_liters),
            )
            moisture, sensor_depletion = self._soil_reading(zone)
            samples = zone.state["throughput_samples"]
            measured = round(median(samples), 1) if samples else None
            rec = model.recommend(params, depletion, conditions, thresholds)
            history = zone.state["history"]
            etc_7d = sum(h["etc"] for h in history[-6:]) + etc_so_far
            snap.zones[sub_id] = ZoneSnapshot(
                depletion_mm=round(depletion, 1),
                committed_depletion_mm=round(zone.depletion, 1),
                soil_water_pct=round((1 - depletion / params.taw_mm) * 100, 0),
                demand_liters=round(depletion / params.efficiency * params.area_m2, 0),
                taw_mm=round(params.taw_mm, 1),
                raw_mm=round(params.raw_mm, 1),
                plant=zone.plant,
                kc=params.kc,
                phase=phase,
                phase_source=phase_source,
                spring_gts=plants.PLANTS[zone.plant].spring_gts if zone.plant in plants.PLANTS else None,
                etc_so_far_mm=round(etc_so_far, 2),
                etc_today_mm=round((et0_so_far + et0_rest_of_day) * params.kc, 2),
                etc_tomorrow_mm=round(tomorrow_totals[0] * zone.kc_for(tomorrow), 2) if tomorrow_totals else None,
                etc_7d_mm=round(etc_7d, 1),
                recommendation=rec,
                sensor_depletion_mm=round(sensor_depletion, 1) if sensor_depletion is not None else None,
                soil_moisture_pct=moisture,
                measured_throughput_lpm=measured,
                current_flow_lpm=round(tracker.current_flow_lpm, 1)
                if tracker and tracker.current_flow_lpm is not None
                else None,
                live_run_liters=round(live_liters, 1),
                faults=self._faults(zone, measured),
                compare_minutes=self._compare_minutes(zone),
                last_run=zone.state.get("last_run"),
                total_liters=zone.state["total_liters"],
                watering=running,
                history=list(history[-7:]),
                soil_wet_pct=zone.soil_wet_pct if zone.data.get(CONF_SOIL_MOISTURE_SENSOR) else None,
                soil_dry_pct=round(zone.soil_dry_pct, 1) if zone.data.get(CONF_SOIL_MOISTURE_SENSOR) else None,
                soil_calibration=zone.soil_calibration if zone.data.get(CONF_SOIL_MOISTURE_SENSOR) else None,
                learned_factor=float(zone.learner.state["factor"]),
                learned_samples=int(zone.learner.state["samples"]),
                learned_recent=list(zone.learner.state["recent"]),
                area_m2=params.area_m2,
                throughput_lpm=params.throughput_lpm,
                et0_today_mm=snap.et0_today_mm,
                last_calculated=dt_util.as_local(now).isoformat(timespec="seconds"),
                data_points=data_points,
            )
        snap.season_status, snap.season_checklist = self._season_status(snap, now)
        snap.weekly = self.weekly_report()
        first = next(iter(snap.zones.values()), None)
        snap.block_reason = " · ".join(first.recommendation.blocked_by) if first else ""
        snap.flow_report, snap.last_run_report = self._run_reports()
        return snap

    def _run_reports(self) -> tuple[str, str]:
        """Texte wie die bisherigen Helfer „gemessener Durchfluss“ und „letzter Lauf“."""
        flows, runs, latest = [], [], None
        for zone in self.zones.values():
            samples = zone.state["throughput_samples"]
            flows.append(f"{zone.name} {_de(samples[-1]) if samples else '?'} l/min")
            last = zone.state.get("last_run")
            if last:
                start = dt_util.as_local(datetime.fromisoformat(last["start"]))
                latest = max(latest, start) if latest else start
                runs.append(f"{zone.name} {round(last['minutes'])} min/{round(last['liters'])} l")
        if latest is None:
            return "Gemessen: noch kein Lauf", "Noch kein Lauf erfasst"
        stamp = latest.strftime("%d.%m.")
        return f"Gemessen {stamp}: " + "; ".join(flows) + ";", f"{latest.strftime('%d.%m. %H:%M')}: " + "; ".join(
            runs
        ) + ";"

    def _ensemble_expected(self, now: datetime) -> float | None:
        if self.ensemble is None:
            return None
        sums = self.ensemble.member_sums(now, 24)
        return round(sum(sums) / len(sums), 1) if sums else None

    # ------------------------------------------------------------------ Winter-Assistent
    def _season_status(self, snap: Snapshot, now: datetime) -> tuple[str, list[str]]:
        """Saisonstatus der Anlage und passende Checkliste; meldet Wechsel einmalig."""
        assert self.weather is not None
        phases = [z.phase for z in snap.zones.values() if z.phase is not None]
        all_dormant = bool(phases) and all(p == plants.PHASE_DORMANT for p in phases)
        temps = [h.temp_c for h in self.weather.next_hours(now, 72) if h.temp_c is not None]
        frost_soon = bool(temps) and min(temps) <= WINTER_FROST_C
        has_banana = any(z.plant == "banana" for z in self.zones.values())

        if self.winterized:
            status = STATUS_PREPARE_SPRING if phases and not all_dormant else STATUS_WINTERIZED
        elif all_dormant:
            status = STATUS_WINTERIZE if frost_soon else STATUS_RESTING
        else:
            status = STATUS_SEASON

        checklist: list[str] = []
        if status == STATUS_WINTERIZE:
            checklist = [
                "Wasserzufuhr und Bewässerungspumpe abstellen",
                "Leitungen, Rohre und Ventile entleeren (Ventile offen lassen)",
                "Batterien (4× AA) aus den Ventilen nehmen",
                "Pumpe und Filter frostfrei lagern",
            ]
            if has_banana:
                checklist.append("Bananen einpacken oder einräumen")
            checklist.append("Danach Service „Winterfest setzen“ aufrufen")
        elif status == STATUS_PREPARE_SPRING:
            checklist = [
                "Leitungen und Ventile auf Frostschäden prüfen",
                "Filter reinigen, Batterien einsetzen",
                "Jede Zone kurz testen und den Volumenstrom prüfen",
                "Danach Service „Winterfest setzen“ mit „aus“ aufrufen",
            ]
            if has_banana:
                checklist.insert(0, "Bananen-Schutz entfernen, sobald kein Frost mehr droht")

        notified = self._stored.get("season_notified")
        if status in (STATUS_WINTERIZE, STATUS_PREPARE_SPRING) and notified != status:
            title = (
                "Bewässerung winterfest machen"
                if status == STATUS_WINTERIZE
                else "Bewässerung für die Saison vorbereiten"
            )
            persistent_notification.async_create(
                self.hass,
                "\n".join(f"- {item}" for item in checklist),
                title=title,
                notification_id=f"{DOMAIN}_season",
            )
            self._stored["season_notified"] = status
            self._save()
        elif status == STATUS_SEASON and notified is not None:
            self._stored["season_notified"] = None
            self._save()
        return status, checklist

    # ------------------------------------------------------------------ Wochenbericht
    def weekly_report(self) -> dict[str, Any]:
        """Bilanz der letzten 7 abgeschlossenen Tage je Zone."""
        last_closed = date.fromisoformat(self._stored["last_closed"])
        start = last_closed - timedelta(days=6)
        days = {(start + timedelta(days=i)).isoformat() for i in range(7)}
        price = self.water_price
        zones: dict[str, Any] = {}
        lines: list[str] = []
        total_liters = 0.0
        for zone in self.zones.values():
            history = [h for h in zone.state["history"] if h["date"] in days]
            liters = sum(v for k, v in zone.state["liters_by_date"].items() if k in days)
            runs = sum(v for k, v in zone.state["runs_by_date"].items() if k in days)
            etc = sum(h["etc"] for h in history)
            rain = sum(h["effective_rain"] for h in history)
            irrigation = sum(h["irrigation_mm"] for h in history)
            total_liters += liters
            entry = {
                "liters": round(liters),
                "runs": runs,
                "irrigation_mm": round(irrigation, 1),
                "use_mm": round(etc, 1),
                "effective_rain_mm": round(rain, 1),
                "depletion_mm": round(zone.depletion, 1),
                "learned_factor": float(zone.learner.state["factor"]),
                "days": len(history),
            }
            if price > 0:
                entry["cost_eur"] = round(liters / 1000 * price, 2)
            zones[zone.name] = entry
            line = (
                f"{zone.name}: {entry['liters']} l in {runs} Läufen, Verbrauch {_de(etc)} mm, "
                f"Regen {_de(rain)} mm, gegossen {_de(irrigation)} mm"
            )
            if price > 0:
                line += f", {_de(entry['cost_eur'], 2)} €"
            lines.append(line)
        period = f"{start.strftime('%d.%m.')}–{last_closed.strftime('%d.%m.%Y')}"
        text = f"Bewässerung {period}: {round(total_liters)} l\n" + "\n".join(lines)
        return {
            "period_start": start.isoformat(),
            "period_end": last_closed.isoformat(),
            "total_liters": round(total_liters),
            "total_cost_eur": round(total_liters / 1000 * price, 2) if price > 0 else None,
            "zones": zones,
            "text": text,
        }

    # ------------------------------------------------------------------ Hilfen
    def zone_for_subentry(self, subentry_id: str) -> Zone | None:
        """Zone zu einer Subentry-ID."""
        return self.zones.get(subentry_id)

    def diagnostics(self) -> dict[str, Any]:
        """Rohdaten für den Diagnose-Download."""
        return {
            "stored": self._stored,
            "weather_daily": {d.isoformat(): vars(w) for d, w in (self.weather.daily.items() if self.weather else [])},
        }
