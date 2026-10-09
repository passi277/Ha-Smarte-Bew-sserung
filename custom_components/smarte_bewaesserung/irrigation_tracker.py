"""Erkennt echte Bewässerungsläufe an Ventil und Volumenstrom.

Der Tracker schaltet nichts. Er beobachtet nur, wann Wasser fließt, und misst
die Menge, egal ob der Lauf von Smart Irrigation, einem Skript oder von Hand
gestartet wurde.

Mengenquellen in dieser Reihenfolge:
1. Volumenstrom-Sensor (z. B. m³/h), über die Laufzeit aufsummiert
2. Mengenzähler des Ventils (Differenz vor/nach dem Lauf)
3. Schätzung aus Laufzeit × eingestelltem Durchsatz
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import NO_FLOW_GRACE_SECONDS, STUCK_OPEN_EXTRA_MIN, VOLUME_SETTLE_SECONDS

_LOGGER = logging.getLogger(__name__)

OPEN_STATES = {"on", "open", "opening"}
INVALID_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, None, ""}

# Umrechnung gängiger Volumeneinheiten in Liter.
VOLUME_UNITS = {"l": 1.0, "L": 1.0, "ml": 0.001, "mL": 0.001, "m³": 1000.0, "gal": 3.78541}
# Umrechnung gängiger Durchflusseinheiten in l/min.
FLOW_UNITS = {
    "m³/h": 1000 / 60,
    "m³/min": 1000.0,
    "m³/s": 60000.0,
    "L/min": 1.0,
    "l/min": 1.0,
    "L/h": 1 / 60,
    "l/h": 1 / 60,
    "L/s": 60.0,
    "mL/s": 0.06,
    "gal/min": 3.78541,
}

# Gemessene Menge gilt als plausibel, wenn sie in diesem Verhältnis zur erwarteten liegt.
PLAUSIBLE_RATIO = (0.2, 3.0)

SOURCE_FLOW = "flow"
SOURCE_VOLUME = "volume"
SOURCE_ESTIMATE = "estimate"


@dataclass
class RunRecord:
    """Ein abgeschlossener Lauf."""

    start: datetime
    end: datetime
    minutes: float
    liters: float
    measured: bool
    no_flow: bool = False
    implausible_volume: bool = False
    source: str = SOURCE_ESTIMATE

    def as_dict(self) -> dict:
        """Für Storage und Attribute."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "minutes": round(self.minutes, 1),
            "liters": round(self.liters, 1),
            "measured": self.measured,
            "source": self.source,
            "no_flow": self.no_flow,
            "implausible_volume": self.implausible_volume,
        }


def _state_float(hass: HomeAssistant, entity_id: str | None) -> tuple[float, str] | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    try:
        return float(state.state), state.attributes.get("unit_of_measurement") or ""
    except ValueError:
        return None


def read_liters(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Volumensensor in Litern lesen."""
    reading = _state_float(hass, entity_id)
    if reading is None:
        return None
    value, unit = reading
    return value * VOLUME_UNITS.get(unit or "L", 1.0)


def read_flow_lpm(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Volumenstrom in l/min lesen."""
    reading = _state_float(hass, entity_id)
    if reading is None:
        return None
    value, unit = reading
    return max(value, 0.0) * FLOW_UNITS.get(unit or "L/min", 1.0)


def read_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Beliebigen numerischen Zustand lesen."""
    reading = _state_float(hass, entity_id)
    return reading[0] if reading else None


def measured_liters(start: float | None, end: float | None) -> float | None:
    """Menge aus Zählerstand vor und nach dem Lauf.

    Fällt der Zähler während des Laufs (Tagesreset oder Zähler pro Lauf), zählt
    der Endstand allein. Bleibt er gleich, gibt es keine Messung.
    """
    if start is None or end is None:
        return None
    if end > start:
        return end - start
    if end < start:
        return end
    return None


class IrrigationTracker:
    """Beobachtet Ventil und Volumenstrom einer Zone.

    Ohne Ventil-Entity gilt „Volumenstrom > 0“ als laufende Bewässerung.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        valve_entity: str | None,
        volume_sensor: str | None,
        flow_sensor: str | None,
        throughput_lpm: float,
        max_duration_min: float,
        on_run: Callable[[RunRecord], None],
        on_change: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.valve_entity = valve_entity
        self.volume_sensor = volume_sensor
        self.flow_sensor = flow_sensor
        self.throughput_lpm = throughput_lpm
        self.max_duration_min = max_duration_min
        self._on_run = on_run
        self._on_change = on_change
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timers: list[CALLBACK_TYPE] = []
        self._pending_finish: list[CALLBACK_TYPE] = []
        self._start: datetime | None = None
        self._start_volume: float | None = None
        self._no_flow = False
        self._flow_liters = 0.0
        self._flow_seen = False
        self._flow_lpm = 0.0
        self._flow_since: datetime | None = None
        self.stuck_open = False

    @property
    def running(self) -> bool:
        """Läuft gerade ein Lauf?"""
        return self._start is not None

    @property
    def valve_available(self) -> bool:
        """Ist das Ventil erreichbar?"""
        if not self.valve_entity:
            return True
        state = self.hass.states.get(self.valve_entity)
        return state is not None and state.state not in INVALID_STATES

    @property
    def current_flow_lpm(self) -> float | None:
        """Aktueller Volumenstrom in l/min."""
        return read_flow_lpm(self.hass, self.flow_sensor)

    @property
    def live_liters(self) -> float:
        """Bisher ausgebrachte Liter des laufenden Laufs."""
        if self._start is None:
            return 0.0
        now = dt_util.utcnow()
        if self.flow_sensor and self._flow_seen:
            since = self._flow_since or now
            return self._flow_liters + self._flow_lpm * (now - since).total_seconds() / 60
        return (now - self._start).total_seconds() / 60 * self.throughput_lpm

    @property
    def live_minutes(self) -> float:
        """Bisherige Laufzeit des laufenden Laufs."""
        if self._start is None:
            return 0.0
        return (dt_util.utcnow() - self._start).total_seconds() / 60

    @callback
    def async_start(self) -> None:
        """Beobachtung starten; ein bereits laufender Lauf wird übernommen."""
        if self.valve_entity:
            self._unsubs.append(async_track_state_change_event(self.hass, [self.valve_entity], self._valve_changed))
        if self.flow_sensor:
            self._unsubs.append(async_track_state_change_event(self.hass, [self.flow_sensor], self._flow_changed))
        if self._is_open():
            self._run_started()

    @callback
    def async_stop(self) -> None:
        """Alle Listener lösen."""
        for unsub in self._unsubs + self._timers + self._pending_finish:
            unsub()
        self._unsubs.clear()
        self._timers.clear()
        self._pending_finish.clear()

    def _is_open(self) -> bool:
        if self.valve_entity:
            state = self.hass.states.get(self.valve_entity)
            return state is not None and state.state in OPEN_STATES
        return (self.current_flow_lpm or 0.0) > 0

    @callback
    def _valve_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        is_open = new is not None and new.state in OPEN_STATES
        was_open = old is not None and old.state in OPEN_STATES
        if is_open and not was_open and not self.running:
            self._run_started()
        elif not is_open and self.running and new is not None and new.state not in INVALID_STATES:
            self._run_ended()
        self._on_change()

    @callback
    def _flow_changed(self, event: Event[EventStateChangedData]) -> None:
        now = dt_util.utcnow()
        flow = self.current_flow_lpm
        if self.running:
            self._accumulate(now)
            if flow is not None:
                self._flow_lpm = flow
                if flow > 0:
                    self._flow_seen = True
                    self._no_flow = False
        if not self.valve_entity:
            if flow and flow > 0 and not self.running:
                self._run_started()
            elif flow == 0 and self.running:
                self._run_ended()
        self._on_change()

    @callback
    def _accumulate(self, now: datetime) -> None:
        """Bisherigen Volumenstrom bis jetzt aufsummieren."""
        if self._flow_since is not None:
            self._flow_liters += self._flow_lpm * (now - self._flow_since).total_seconds() / 60
        self._flow_since = now

    @callback
    def _run_started(self) -> None:
        now = dt_util.utcnow()
        self._start = now
        self._start_volume = read_liters(self.hass, self.volume_sensor)
        self._no_flow = False
        self._flow_liters = 0.0
        flow = self.current_flow_lpm
        self._flow_lpm = flow or 0.0
        self._flow_seen = bool(flow)
        self._flow_since = now
        self.stuck_open = False
        self._cancel_timers()
        if self.flow_sensor and self.valve_entity:
            self._timers.append(async_call_later(self.hass, NO_FLOW_GRACE_SECONDS, self._check_flow))
        self._timers.append(
            async_call_later(
                self.hass,
                (self.max_duration_min + STUCK_OPEN_EXTRA_MIN) * 60,
                self._check_stuck,
            )
        )
        _LOGGER.debug("%s: Lauf gestartet", self.valve_entity or self.flow_sensor)

    @callback
    def _check_flow(self, _now: datetime) -> None:
        if self.running and not self._flow_seen and self.current_flow_lpm is not None:
            self._no_flow = True
            _LOGGER.warning("%s ist offen, aber %s meldet keinen Durchfluss", self.valve_entity, self.flow_sensor)
            self._on_change()

    @callback
    def _check_stuck(self, _now: datetime) -> None:
        if self.running:
            self.stuck_open = True
            _LOGGER.warning("%s ist länger offen als erwartet", self.valve_entity or self.flow_sensor)
            self._on_change()

    @callback
    def _run_ended(self) -> None:
        start = self._start
        end = dt_util.utcnow()
        self._accumulate(end)
        start_volume = self._start_volume
        no_flow = self._no_flow
        flow_liters = self._flow_liters if self._flow_seen else None
        self._start = None
        self._flow_since = None
        self.stuck_open = False
        self._cancel_timers()
        if start is None:
            return

        unsub_finish: CALLBACK_TYPE | None = None

        @callback
        def _finish(_now: datetime) -> None:
            if unsub_finish is not None and unsub_finish in self._pending_finish:
                self._pending_finish.remove(unsub_finish)
            minutes = (end - start).total_seconds() / 60
            expected = minutes * self.throughput_lpm
            candidates = [
                (SOURCE_FLOW, flow_liters),
                (SOURCE_VOLUME, measured_liters(start_volume, read_liters(self.hass, self.volume_sensor))),
            ]
            liters, source, implausible = None, SOURCE_ESTIMATE, False
            for candidate_source, value in candidates:
                if value is None or value <= 0:
                    continue
                ratio = value / expected if expected > 0 else 1.0
                if PLAUSIBLE_RATIO[0] <= ratio <= PLAUSIBLE_RATIO[1]:
                    liters, source = value, candidate_source
                    break
                implausible = True
            record = RunRecord(
                start=start,
                end=end,
                minutes=minutes,
                liters=liters if liters is not None else expected,
                measured=liters is not None,
                no_flow=no_flow,
                implausible_volume=implausible and liters is None,
                source=source,
            )
            _LOGGER.debug("%s: Lauf beendet %s", self.valve_entity or self.flow_sensor, record)
            self._on_run(record)

        if self.volume_sensor and flow_liters is None:
            # Ventilzähler melden die Menge oft erst einige Sekunden nach dem Schließen.
            unsub_finish = async_call_later(self.hass, VOLUME_SETTLE_SECONDS, _finish)
            self._pending_finish.append(unsub_finish)
        else:
            _finish(end)

    @callback
    def _cancel_timers(self) -> None:
        for unsub in self._timers:
            unsub()
        self._timers.clear()
