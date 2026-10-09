"""Erkennt echte Bewässerungsläufe an den Ventil-Entities.

Der Tracker schaltet nichts. Er beobachtet nur, wann ein Ventil auf- und
zugeht, und misst die Wassermenge, egal ob der Lauf von Smart Irrigation,
einem Skript oder von Hand gestartet wurde.
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

# Gemessene Menge gilt als plausibel, wenn sie in diesem Verhältnis zur erwarteten liegt.
PLAUSIBLE_RATIO = (0.2, 3.0)


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

    def as_dict(self) -> dict:
        """Für Storage und Attribute."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "minutes": round(self.minutes, 1),
            "liters": round(self.liters, 1),
            "measured": self.measured,
            "no_flow": self.no_flow,
            "implausible_volume": self.implausible_volume,
        }


def read_liters(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Volumensensor in Litern lesen."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement") or "L"
    return value * VOLUME_UNITS.get(unit, 1.0)


def read_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Beliebigen numerischen Zustand lesen."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


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
    """Beobachtet das Ventil einer Zone."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        valve_entity: str,
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
        self.stuck_open = False

    @property
    def running(self) -> bool:
        """Läuft gerade ein Lauf?"""
        return self._start is not None

    @property
    def valve_available(self) -> bool:
        """Ist das Ventil erreichbar?"""
        state = self.hass.states.get(self.valve_entity)
        return state is not None and state.state not in INVALID_STATES

    @callback
    def async_start(self) -> None:
        """Beobachtung starten; ein bereits offenes Ventil wird übernommen."""
        self._unsubs.append(async_track_state_change_event(self.hass, [self.valve_entity], self._valve_changed))
        state = self.hass.states.get(self.valve_entity)
        if state is not None and state.state in OPEN_STATES:
            self._run_started()

    @callback
    def async_stop(self) -> None:
        """Alle Listener lösen."""
        for unsub in self._unsubs + self._timers + self._pending_finish:
            unsub()
        self._unsubs.clear()
        self._timers.clear()
        self._pending_finish.clear()

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
    def _run_started(self) -> None:
        self._start = dt_util.utcnow()
        self._start_volume = read_liters(self.hass, self.volume_sensor)
        self._no_flow = False
        self.stuck_open = False
        self._cancel_timers()
        if self.flow_sensor:
            self._timers.append(async_call_later(self.hass, NO_FLOW_GRACE_SECONDS, self._check_flow))
        self._timers.append(
            async_call_later(
                self.hass,
                (self.max_duration_min + STUCK_OPEN_EXTRA_MIN) * 60,
                self._check_stuck,
            )
        )
        _LOGGER.debug("%s: Lauf gestartet", self.valve_entity)

    @callback
    def _check_flow(self, _now: datetime) -> None:
        flow = read_float(self.hass, self.flow_sensor)
        if self.running and flow is not None and flow <= 0:
            self._no_flow = True
            _LOGGER.warning("%s ist offen, aber %s meldet keinen Durchfluss", self.valve_entity, self.flow_sensor)
            self._on_change()

    @callback
    def _check_stuck(self, _now: datetime) -> None:
        if self.running:
            self.stuck_open = True
            _LOGGER.warning("%s ist länger offen als erwartet", self.valve_entity)
            self._on_change()

    @callback
    def _run_ended(self) -> None:
        start = self._start
        start_volume = self._start_volume
        no_flow = self._no_flow
        end = dt_util.utcnow()
        self._start = None
        self.stuck_open = False
        self._cancel_timers()
        if start is None:
            return

        @callback
        def _finish(_now: datetime) -> None:
            if unsub_finish is not None and unsub_finish in self._pending_finish:
                self._pending_finish.remove(unsub_finish)
            minutes = (end - start).total_seconds() / 60
            expected = minutes * self.throughput_lpm
            liters = measured_liters(start_volume, read_liters(self.hass, self.volume_sensor))
            implausible = False
            if liters is not None and expected > 0:
                ratio = liters / expected
                if not PLAUSIBLE_RATIO[0] <= ratio <= PLAUSIBLE_RATIO[1]:
                    implausible = True
                    liters = None
            measured = liters is not None
            record = RunRecord(
                start=start,
                end=end,
                minutes=minutes,
                liters=liters if measured else expected,
                measured=measured,
                no_flow=no_flow,
                implausible_volume=implausible,
            )
            _LOGGER.debug("%s: Lauf beendet %s", self.valve_entity, record)
            self._on_run(record)

        unsub_finish: CALLBACK_TYPE | None = None
        if self.volume_sensor:
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
