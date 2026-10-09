"""Tests für Setup, Tagesabschluss, Lauferkennung und Services."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.smarte_bewaesserung.const import (
    CONF_AREA,
    CONF_COMPARE_ENTITY,
    CONF_DEPLETION_FRACTION,
    CONF_IRRIGATION_TYPE,
    CONF_KC,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_RAIN_SENSOR,
    CONF_ROOT_DEPTH,
    CONF_SOIL_DRY_PCT,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_SOIL_TYPE,
    CONF_SOIL_WET_PCT,
    CONF_THROUGHPUT,
    CONF_VALVE,
    CONF_VOLUME_SENSOR,
    DOMAIN,
    SUBENTRY_ZONE,
)

from .conftest import make_weather

VALVE = "switch.ventil_haus"
VOLUME = "sensor.ventil_haus_volume"
SOIL = "sensor.boden_haus"
COMPARE = "sensor.smart_irrigation_haus"

ZONE_DATA = {
    "name": "Haus",
    CONF_AREA: 120,
    CONF_THROUGHPUT: 17,
    CONF_IRRIGATION_TYPE: "sprinkler",
    CONF_KC: 0.8,
    CONF_SOIL_TYPE: "loam",
    CONF_ROOT_DEPTH: 20,
    CONF_DEPLETION_FRACTION: 0.5,
    CONF_MIN_DURATION: 3,
    CONF_MAX_DURATION: 30,
    CONF_VALVE: VALVE,
    CONF_VOLUME_SENSOR: VOLUME,
    CONF_SOIL_MOISTURE_SENSOR: SOIL,
    CONF_SOIL_DRY_PCT: 10,
    CONF_SOIL_WET_PCT: 40,
    CONF_COMPARE_ENTITY: COMPARE,
}


def _entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Smarte Bewässerung",
        unique_id=DOMAIN,
        options=options or {},
        subentries_data=[{"data": ZONE_DATA, "subentry_type": SUBENTRY_ZONE, "title": "Haus", "unique_id": None}],
    )


def _eid(hass: HomeAssistant, domain: str, key: str) -> str:
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.platform == DOMAIN and entry.domain == domain and entry.unique_id.endswith(f"_{key}"):
            return entry.entity_id
    raise AssertionError(f"{domain}/{key} nicht gefunden")


@pytest.fixture
async def setup(hass: HomeAssistant, mock_fetch, freezer: FrozenDateTimeFactory):
    """Integration mit einer Zone einrichten."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-07-10 08:00:00+02:00")
    mock_fetch.return_value = make_weather(dt_util.now().date(), et0=5.0)
    hass.states.async_set(VALVE, "off")
    hass.states.async_set(VOLUME, "0", {"unit_of_measurement": "L"})
    hass.states.async_set(COMPARE, "600", {"unit_of_measurement": "s"})
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities(hass: HomeAssistant, setup) -> None:
    assert setup.state is ConfigEntryState.LOADED
    assert hass.states.get(_eid(hass, "sensor", "depletion")).state == "0.0"
    assert hass.states.get(_eid(hass, "sensor", "et0_today")).state == "5.0"
    assert hass.states.get(_eid(hass, "binary_sensor", "irrigation_recommended")).state == "off"
    assert hass.states.get(_eid(hass, "binary_sensor", "watering")).state == "off"
    # Vergleich: 0 min eigene Empfehlung − 10 min Smart Irrigation
    assert hass.states.get(_eid(hass, "sensor", "compare_smart_irrigation")).state == "-10.0"
    # Zonen-Entities hängen am Subentry
    entity = er.async_get(hass).async_get(_eid(hass, "sensor", "depletion"))
    assert entity.config_subentry_id == next(iter(setup.subentries))


async def test_days_close_and_recommend(hass: HomeAssistant, setup, mock_fetch, freezer: FrozenDateTimeFactory) -> None:
    depletion = _eid(hass, "sensor", "depletion")
    # Vier trockene Tage mit ET0 5 mm → je 4 mm Defizit
    for _ in range(4):
        freezer.tick(timedelta(days=1))
        mock_fetch.return_value = make_weather(dt_util.now().date(), et0=5.0)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert float(hass.states.get(depletion).state) == pytest.approx(16.0)
    recommended = hass.states.get(_eid(hass, "binary_sensor", "irrigation_recommended"))
    assert recommended.state == "on"
    duration = hass.states.get(_eid(hass, "sensor", "recommended_duration"))
    assert duration.state == "30"
    assert duration.attributes["capped"] is True
    history = hass.states.get(depletion).attributes["history"]
    assert len(history) == 4
    assert history[-1]["etc"] == 4.0


async def test_rain_forecast_blocks(hass: HomeAssistant, setup, mock_fetch, freezer: FrozenDateTimeFactory) -> None:
    coordinator = setup.runtime_data
    zone = next(iter(coordinator.zones.values()))
    coordinator.set_depletion(zone, 20)
    mock_fetch.return_value = make_weather(dt_util.now().date(), forecast_rain_per_hour=0.5)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "binary_sensor", "irrigation_recommended")).state == "off"
    assert hass.states.get(_eid(hass, "binary_sensor", "rain_block")).state == "on"
    assert "Regen angesagt" in hass.states.get(_eid(hass, "sensor", "reason")).state


async def test_valve_run_is_tracked(hass: HomeAssistant, setup, freezer: FrozenDateTimeFactory) -> None:
    coordinator = setup.runtime_data
    zone = next(iter(coordinator.zones.values()))
    coordinator.set_depletion(zone, 20)
    await hass.async_block_till_done()

    hass.states.async_set(VALVE, "on")
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "binary_sensor", "watering")).state == "on"

    freezer.tick(timedelta(minutes=10))
    hass.states.async_set(VOLUME, "170", {"unit_of_measurement": "L"})
    hass.states.async_set(VALVE, "off")
    await hass.async_block_till_done()
    # Menge wird erst nach der Nachlaufzeit gelesen
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    last_run = hass.states.get(_eid(hass, "sensor", "last_run_volume"))
    assert float(last_run.state) == pytest.approx(170)
    assert last_run.attributes["measured"] is True
    # 170 l / 120 m² * 0,75 = 1,0625 mm
    assert float(hass.states.get(_eid(hass, "sensor", "depletion")).state) == pytest.approx(18.9, abs=0.05)
    assert float(hass.states.get(_eid(hass, "sensor", "measured_throughput")).state) == pytest.approx(17.0)
    assert float(hass.states.get(_eid(hass, "sensor", "water_total")).state) == pytest.approx(170)


async def test_implausible_volume_is_estimated(hass: HomeAssistant, setup, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set(VOLUME, "500", {"unit_of_measurement": "L"})
    hass.states.async_set(VALVE, "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    # Zähler springt zurück auf 1 l: wie im echten System (−148 l / 1 l) → geschätzt
    hass.states.async_set(VOLUME, "1", {"unit_of_measurement": "L"})
    hass.states.async_set(VALVE, "off")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    last_run = hass.states.get(_eid(hass, "sensor", "last_run_volume"))
    assert last_run.attributes["measured"] is False
    assert float(last_run.state) == pytest.approx(170)
    problem = hass.states.get(_eid(hass, "binary_sensor", "problem"))
    assert problem.state == "on"
    assert any("unplausibel" in f for f in problem.attributes["faults"])


async def test_soil_sensor_blends_at_day_close(
    hass: HomeAssistant, setup, mock_fetch, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOIL, "25", {"unit_of_measurement": "%"})  # entspricht 15 mm Erschöpfung
    freezer.tick(timedelta(days=1))
    mock_fetch.return_value = make_weather(dt_util.now().date(), et0=5.0)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # Modell 4 mm, Sensor 15 mm, Gewicht 0,5 → 9,5 mm
    assert float(hass.states.get(_eid(hass, "sensor", "depletion")).state) == pytest.approx(9.5)


async def test_services(hass: HomeAssistant, setup) -> None:
    depletion = _eid(hass, "sensor", "depletion")
    await hass.services.async_call(
        DOMAIN, "set_depletion", {"entity_id": depletion, "soil_water_pct": 50}, blocking=True
    )
    assert float(hass.states.get(depletion).state) == pytest.approx(15.0)

    await hass.services.async_call(DOMAIN, "record_irrigation", {"entity_id": depletion, "liters": 340}, blocking=True)
    assert float(hass.states.get(depletion).state) == pytest.approx(12.9, abs=0.05)

    hass.states.async_set(SOIL, "12", {"unit_of_measurement": "%"})
    result = await hass.services.async_call(
        DOMAIN,
        "calibrate_soil_sensor",
        {"entity_id": depletion, "point": "dry"},
        blocking=True,
        return_response=True,
    )
    assert result == {"point": "dry", "moisture_pct": 12.0}


async def test_rain_sensor_replaces_open_meteo(hass: HomeAssistant, mock_fetch, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-07-10 08:00:00+02:00")
    mock_fetch.return_value = make_weather(dt_util.now().date(), et0=2.0, rain=30.0)
    hass.states.async_set("sensor.regen", "100.0", {"unit_of_measurement": "mm"})
    hass.states.async_set(VALVE, "off")
    entry = _entry({CONF_RAIN_SENSOR: "sensor.regen"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.regen", "101.5", {"unit_of_measurement": "mm"})
    await hass.async_block_till_done()
    rain_today = hass.states.get(_eid(hass, "sensor", "rain_today"))
    assert float(rain_today.state) == pytest.approx(1.5)
    assert rain_today.attributes["source"] == "rain_sensor"

    freezer.tick(timedelta(days=1))
    mock_fetch.return_value = make_weather(dt_util.now().date(), et0=2.0, rain=30.0)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # Sensor: 1,5 mm → wirksam 0,9 mm; ETc 1,6 mm → 0,7 mm (Open-Meteo hätte 0 ergeben)
    assert float(hass.states.get(_eid(hass, "sensor", "depletion")).state) == pytest.approx(0.7)


async def test_unload(hass: HomeAssistant, setup) -> None:
    assert await hass.config_entries.async_unload(setup.entry_id)
    await hass.async_block_till_done()
    assert setup.state is ConfigEntryState.NOT_LOADED
