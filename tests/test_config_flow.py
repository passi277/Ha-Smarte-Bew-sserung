"""Tests für den Config Flow und die Zonen-Subentries."""

from __future__ import annotations

from datetime import date

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smarte_bewaesserung.const import (
    CONF_AREA,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_RAIN_SKIP_MM,
    CONF_SOIL_DRY_PCT,
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


async def test_zone_subentry_flow(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.return_value = make_weather(date.today())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Smarte Bewässerung")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    base = {"name": "Bananen", CONF_AREA: 15, CONF_THROUGHPUT: 20}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**base, CONF_MIN_DURATION: 40, CONF_MAX_DURATION: 30}
    )
    assert result["errors"] == {CONF_MIN_DURATION: "min_above_max"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**base, CONF_SOIL_DRY_PCT: 40, CONF_SOIL_WET_PCT: 20}
    )
    assert result["errors"] == {CONF_SOIL_WET_PCT: "wet_not_above_dry"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], base)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "Bananen"
    # Neu geladen: Zone ist im Coordinator
    assert [z.name for z in entry.runtime_data.zones.values()] == ["Bananen"]

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**dict(subentry.data), CONF_AREA: 18}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry.subentry_id].data[CONF_AREA] == 18


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
