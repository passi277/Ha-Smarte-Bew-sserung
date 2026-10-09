"""Tests für den Config Flow und die Zonen-Subentries."""

from __future__ import annotations

from datetime import date

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smarte_bewaesserung.const import (
    CONF_AREA,
    CONF_DEPLETION_FRACTION,
    CONF_IRRIGATION_TYPE,
    CONF_KC_FACTOR,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_PLANT,
    CONF_RAIN_SKIP_MM,
    CONF_ROOT_DEPTH,
    CONF_SOIL_DRY_PCT,
    CONF_SOIL_TYPE,
    CONF_SOIL_WET_PCT,
    CONF_THROUGHPUT,
    DOMAIN,
    SUBENTRY_ZONE,
)

from .conftest import make_weather


async def test_user_flow(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.return_value = make_weather(date.today())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_RAIN_SKIP_MM: 4})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_RAIN_SKIP_MM] == 4

    # Nur eine Instanz
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


DETAILS = {
    CONF_IRRIGATION_TYPE: "drip",
    CONF_KC_FACTOR: 1.0,
    CONF_SOIL_TYPE: "loam",
    CONF_ROOT_DEPTH: 40,
    CONF_DEPLETION_FRACTION: 0.35,
    CONF_MIN_DURATION: 3,
    CONF_MAX_DURATION: 30,
    CONF_SOIL_DRY_PCT: 10,
    CONF_SOIL_WET_PCT: 40,
}


def _suggested(result, key):
    for field in result["data_schema"].schema:
        if field == key:
            return field.description["suggested_value"]
    raise AssertionError(key)


async def test_zone_subentry_flow(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.return_value = make_weather(date.today())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Smarte Bewässerung")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE), context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Bananen", CONF_PLANT: "banana", CONF_AREA: 15, CONF_THROUGHPUT: 20}
    )
    assert result["step_id"] == "details"
    # Vorschläge aus dem Bananen-Profil
    assert _suggested(result, CONF_ROOT_DEPTH) == 40
    assert _suggested(result, CONF_DEPLETION_FRACTION) == 0.35
    assert _suggested(result, CONF_IRRIGATION_TYPE) == "drip"
    assert "Jul 1,20" in result["description_placeholders"]["kc_curve"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**DETAILS, CONF_MIN_DURATION: 40, CONF_MAX_DURATION: 30}
    )
    assert result["errors"] == {CONF_MIN_DURATION: "min_above_max"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**DETAILS, CONF_SOIL_DRY_PCT: 40, CONF_SOIL_WET_PCT: 20}
    )
    assert result["errors"] == {CONF_SOIL_WET_PCT: "wet_not_above_dry"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], DETAILS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "Bananen"
    assert subentry.data[CONF_PLANT] == "banana"
    # Neu geladen: Zone ist im Coordinator
    assert [z.name for z in entry.runtime_data.zones.values()] == ["Bananen"]

    # Ändern: auf Rasen umstellen → Rasen-Vorschläge für Wurzeltiefe
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Bananen", CONF_PLANT: "lawn", CONF_AREA: 18, CONF_THROUGHPUT: 20}
    )
    assert result["step_id"] == "reconfigure_details"
    assert _suggested(result, CONF_ROOT_DEPTH) == 20
    assert _suggested(result, CONF_SOIL_TYPE) == "loam"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], DETAILS)
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry.subentry_id].data[CONF_AREA] == 18
    assert entry.subentries[subentry.subentry_id].data[CONF_PLANT] == "lawn"


async def test_options_flow(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.return_value = make_weather(date.today())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_RAIN_SKIP_MM: 3})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_RAIN_SKIP_MM: 6})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.thresholds.rain_skip_mm == 6
