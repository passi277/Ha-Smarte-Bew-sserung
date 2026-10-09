"""Coordinator: Wetter holen, Tage abschließen, Empfehlungen berechnen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import model
from .const import (
    CONF_AREA,
    CONF_COMPARE_ENTITY,
    CONF_DEPLETION_FRACTION,
    CONF_FLOW_SENSOR,
    CONF_FROST_C,
    CONF_IRRIGATION_TYPE,
    CONF_KC,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
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
    CONF_WIND_KMH,
    DEFAULT_FROST_C,
    DEFAULT_RAIN_SKIP_MM,
    DEFAULT_SOIL_DRY_PCT,
    DEFAULT_SOIL_SENSOR_WEIGHT,
    DEFAULT_SOIL_WET_PCT,
    DEFAULT_WIND_KMH,
    DOMAIN,
    HISTORY_DAYS,
    STORAGE_VERSION,
    SUBENTRY_ZONE,
    THROUGHPUT_DEVIATION,
    THROUGHPUT_SAMPLES,
    UPDATE_INTERVAL,
)
from .irrigation_tracker import INVALID_STATES, IrrigationTracker, RunRecord, read_float
from .open_meteo import OpenMeteoError, WeatherData, async_fetch

_LOGGER = logging.getLogger(__name__)

RATE_UNITS = ("mm/h", "in/h", "mm/d")
DURATION_UNITS = {"s": 1 / 60, "min": 1.0, "h": 60.0}


def zone_params(data: dict[str, Any]) -> model.ZoneParams:
    """ZoneParams aus Subentry-Daten."""
    return model.ZoneParams(
        area_m2=float(data[CONF_AREA]),
        throughput_lpm=float(data[CONF_THROUGHPUT]),
        kc=float(data[CONF_KC]),
        soil_type=data[CONF_SOIL_TYPE],
        root_depth_cm=float(data[CONF_ROOT_DEPTH]),
        depletion_fraction=float(data[CONF_DEPLETION_FRACTION]),
        irrigation_type=data[CONF_IRRIGATION_TYPE],
        min_duration_min=float(data[CONF_MIN_DURATION]),
        max_duration_min=float(data[CONF_MAX_DURATION]),
    )


@dataclass
class ZoneSnapshot:
    """Berechneter Zustand einer Zone für die Entities."""

    depletion_mm: float
    soil_water_pct: float
    taw_mm: float
    raw_mm: float
    recommendation: model.Recommendation
    sensor_depletion_mm: float | None
    soil_moisture_pct: float | None
    measured_throughput_lpm: float | None
    faults: list[str]
    compare_minutes: float | None
    last_run: dict[str, Any] | None
    total_liters: float
    watering: bool
    history: list[dict[str, Any]]


@dataclass
class Snapshot:
    """Gesamter berechneter Zustand."""

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
    weather_updated: datetime | None = None
    zones: dict[str, ZoneSnapshot] = field(default_factory=dict)


class Zone:
    """Laufzeitobjekt einer Zone (Subentry)."""

    def __init__(self, subentry: ConfigSubentry, state: dict[str, Any]) -> None:
        self.subentry_id = subentry.subentry_id
        self.name = subentry.title
        self.data = dict(subentry.data)
        self.params = zone_params(self.data)
        self.state = state
        state.setdefault("depletion", 0.0)
        state.setdefault("total_liters", 0.0)
        state.setdefault("last_run", None)
        state.setdefault("throughput_samples", [])
        state.setdefault("history", [])
        state.setdefault("irrigation_mm_by_date", {})
        self.tracker: IrrigationTracker | None = None

    @property
    def depletion(self) -> float:
        return float(self.state["depletion"])

    @depletion.setter
    def depletion(self, value: float) -> None:
        self.state["depletion"] = round(model.clamp_depletion(self.params, value), 3)

    @property
    def soil_dry_pct(self) -> float:
        return float(self.state.get("soil_dry_pct", self.data.get(CONF_SOIL_DRY_PCT, DEFAULT_SOIL_DRY_PCT)))

    @property
    def soil_wet_pct(self) -> float:
        return float(self.state.get("soil_wet_pct", self.data.get(CONF_SOIL_WET_PCT, DEFAULT_SOIL_WET_PCT)))


class SmarteBewaesserungCoordinator(DataUpdateCoordinator[Snapshot]):
    """Hält Wetter, Wasserkonten und Empfehlungen aller Zonen."""

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
        )

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
        if "last_closed" not in self._stored:
            # Neu eingerichtet: nicht rückwirkend rechnen, ab heute bilanzieren.
            self._stored["last_closed"] = (dt_util.now().date() - timedelta(days=1)).isoformat()

        for subentry in self.config_entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_ZONE:
                continue
            state = self._stored["zones"].setdefault(subentry.subentry_id, {})
            zone = Zone(subentry, state)
            self.zones[subentry.subentry_id] = zone
            valve = zone.data.get(CONF_VALVE)
            if valve:
                zone.tracker = IrrigationTracker(
                    self.hass,
                    valve_entity=valve,
                    volume_sensor=zone.data.get(CONF_VOLUME_SENSOR),
                    flow_sensor=zone.data.get(CONF_FLOW_SENSOR),
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

    @callback
    def async_shutdown_listeners(self) -> None:
        """Listener und Tracker beenden."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for zone in self.zones.values():
            if zone.tracker:
                zone.tracker.async_stop()

    async def _midnight(self, _now: datetime) -> None:
        await self.async_request_refresh()

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
        return "rate" if unit in RATE_UNITS else "amount"

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
        self.refresh_snapshot()

    def _mark_rain_sensor_day(self) -> None:
        """Tag als vom Regensensor abgedeckt markieren (auch ohne Regen = 0 mm)."""
        if self._rain_sensor_kind() == "amount" and self._rain_reading() is not None:
            self._stored["rain_by_date"].setdefault(dt_util.now().date().isoformat(), 0.0)

    def _rain_for(self, day: date) -> tuple[float | None, str]:
        sensor_value = self._stored["rain_by_date"].get(day.isoformat())
        if sensor_value is not None:
            return float(sensor_value), "rain_sensor"
        if self.weather and day in self.weather.daily:
            return self.weather.daily[day].rain_mm, "open_meteo"
        return None, "open_meteo"

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

        self._mark_rain_sensor_day()
        self._close_days()
        self._prune()
        self._save()
        return self._build_snapshot()

    def _close_days(self) -> None:
        """Alle vollständig vergangenen, noch offenen Tage bilanzieren."""
        assert self.weather is not None
        today = dt_util.now().date()
        last_closed = date.fromisoformat(self._stored["last_closed"])
        day = last_closed + timedelta(days=1)
        while day < today:
            weather = self.weather.daily.get(day)
            rain, source = self._rain_for(day)
            if weather is None or rain is None:
                _LOGGER.warning("Keine Wetterdaten für %s, Tag wird übersprungen", day)
                day += timedelta(days=1)
                continue
            is_yesterday = day == today - timedelta(days=1)
            for zone in self.zones.values():
                result = model.close_day(zone.params, zone.depletion, weather.et0_mm, rain)
                depletion = result.depletion_mm
                moisture, sensor_depletion = self._soil_reading(zone) if is_yesterday else (None, None)
                depletion = model.blend_depletion(depletion, sensor_depletion, self.soil_sensor_weight)
                zone.depletion = depletion
                zone.state["history"].append(
                    {
                        "date": day.isoformat(),
                        "et0": round(weather.et0_mm, 2),
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

    def _prune(self) -> None:
        cutoff = (dt_util.now().date() - timedelta(days=HISTORY_DAYS)).isoformat()
        rain = self._stored["rain_by_date"]
        for key in [k for k in rain if k < cutoff]:
            del rain[key]
        for zone in self.zones.values():
            zone.state["history"] = [h for h in zone.state["history"] if h["date"] >= cutoff]
            by_date = zone.state["irrigation_mm_by_date"]
            for key in [k for k in by_date if k < cutoff]:
                del by_date[key]

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._stored, 5)

    @callback
    def refresh_snapshot(self) -> None:
        """Ohne neuen Wetterabruf neu rechnen und Entities aktualisieren."""
        if self.weather is None:
            return
        self.async_set_updated_data(self._build_snapshot())

    # ------------------------------------------------------------------ Läufe
    @callback
    def _handle_run(self, zone: Zone, run: RunRecord) -> None:
        self.record_run(zone, run)
        self.refresh_snapshot()

    def record_run(self, zone: Zone, run: RunRecord) -> None:
        """Lauf verbuchen: Wasserkonto, Statistik, Durchsatz."""
        zone.depletion = model.apply_irrigation(zone.params, zone.depletion, run.liters)
        key = dt_util.as_local(run.start).date().isoformat()
        by_date = zone.state["irrigation_mm_by_date"]
        by_date[key] = round(by_date.get(key, 0.0) + model.liters_to_mm(zone.params, run.liters), 3)
        zone.state["total_liters"] = round(zone.state["total_liters"] + run.liters, 1)
        zone.state["last_run"] = run.as_dict()
        if run.measured and run.minutes >= 1:
            samples = zone.state["throughput_samples"]
            samples.append(round(run.liters / run.minutes, 2))
            del samples[:-THROUGHPUT_SAMPLES]
        self._save()

    def set_depletion(self, zone: Zone, depletion_mm: float) -> None:
        """Wasserkonto von Hand setzen."""
        zone.depletion = depletion_mm
        self._save()
        self.refresh_snapshot()

    def calibrate_soil(self, zone: Zone, point: str) -> float:
        """Aktuellen Bodenfeuchtewert als trocken/nass speichern."""
        moisture, _ = self._soil_reading(zone)
        if moisture is None:
            raise ValueError("Bodenfeuchtesensor liefert keinen Wert")
        zone.state["soil_dry_pct" if point == "dry" else "soil_wet_pct"] = moisture
        self._save()
        self.refresh_snapshot()
        return moisture

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
            raining = state is not None and state.state == "on"
        elif kind == "rate":
            raining = (read_float(self.hass, self.rain_sensor) or 0.0) > 0
        return model.Conditions(
            rain_forecast_24h_mm=round(sum(h.rain_mm for h in upcoming), 1),
            min_temp_24h_c=min(temps) if temps else None,
            wind_kmh=current.wind_kmh if current else None,
            raining_now=raining,
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
            (CONF_FLOW_SENSOR, "Durchflusssensor"),
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
        today = dt_util.now().date()
        yesterday = today - timedelta(days=1)
        conditions = self._conditions()
        thresholds = self.thresholds
        rain_today, source = self._rain_for(today)
        rain_yesterday, _ = self._rain_for(yesterday)
        today_w = self.weather.daily.get(today)
        yesterday_w = self.weather.daily.get(yesterday)

        snap = Snapshot(
            et0_today_mm=today_w.et0_mm if today_w else None,
            et0_yesterday_mm=yesterday_w.et0_mm if yesterday_w else None,
            rain_today_mm=rain_today,
            rain_yesterday_mm=rain_yesterday,
            rain_source=source,
            rain_forecast_24h_mm=conditions.rain_forecast_24h_mm,
            min_temp_24h_c=conditions.min_temp_24h_c,
            wind_kmh=conditions.wind_kmh,
            raining_now=conditions.raining_now,
            frost=conditions.min_temp_24h_c is not None and conditions.min_temp_24h_c < thresholds.frost_c,
            last_closed=date.fromisoformat(self._stored["last_closed"]),
            weather_updated=self.weather_updated,
        )
        for sub_id, zone in self.zones.items():
            params = zone.params
            moisture, sensor_depletion = self._soil_reading(zone)
            samples = zone.state["throughput_samples"]
            measured = round(median(samples), 1) if samples else None
            rec = model.recommend(params, zone.depletion, conditions, thresholds)
            compare = self._compare_minutes(zone)
            snap.zones[sub_id] = ZoneSnapshot(
                depletion_mm=round(zone.depletion, 1),
                soil_water_pct=round((1 - zone.depletion / params.taw_mm) * 100, 0),
                taw_mm=round(params.taw_mm, 1),
                raw_mm=round(params.raw_mm, 1),
                recommendation=rec,
                sensor_depletion_mm=round(sensor_depletion, 1) if sensor_depletion is not None else None,
                soil_moisture_pct=moisture,
                measured_throughput_lpm=measured,
                faults=self._faults(zone, measured),
                compare_minutes=compare,
                last_run=zone.state.get("last_run"),
                total_liters=zone.state["total_liters"],
                watering=bool(zone.tracker and zone.tracker.running),
                history=list(zone.state["history"][-7:]),
            )
        return snap

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
