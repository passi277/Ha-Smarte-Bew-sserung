"""Smarte Bewässerung: Wasserbilanz nach FAO-56 im Schattenbetrieb."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    ATTR_DEPLETION_MM,
    ATTR_LITERS,
    ATTR_MINUTES,
    ATTR_POINT,
    ATTR_SOIL_WATER_PCT,
    ATTR_WINTERIZED,
    DOMAIN,
    SERVICE_CALIBRATE_SOIL_SENSOR,
    SERVICE_RECALCULATE,
    SERVICE_RECORD_IRRIGATION,
    SERVICE_SET_DEPLETION,
    SERVICE_SET_WINTERIZED,
    SERVICE_WEEKLY_REPORT,
)
from .coordinator import SmarteBewaesserungCoordinator, Zone
from .irrigation_tracker import RunRecord

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SmarteBewaesserungConfigEntry = ConfigEntry[SmarteBewaesserungCoordinator]

ZONE_TARGET = {vol.Required(ATTR_ENTITY_ID): cv.entity_id}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services registrieren."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmarteBewaesserungConfigEntry) -> bool:
    """Integration einrichten."""
    coordinator = SmarteBewaesserungCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.async_on_unload(coordinator.async_shutdown_listeners)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Zonen hinzufügen/ändern/löschen und Optionen wirken nach einem Neuladen.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmarteBewaesserungConfigEntry) -> bool:
    """Integration entladen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SmarteBewaesserungConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _resolve_zone(hass: HomeAssistant, entity_id: str) -> tuple[SmarteBewaesserungCoordinator, Zone]:
    """Zone über eine beliebige Entity dieser Zone finden."""
    entry_reg = er.async_get(hass).async_get(entity_id)
    if entry_reg is None or entry_reg.platform != DOMAIN or entry_reg.config_subentry_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_zone", translation_placeholders={"entity": entity_id}
        )
    config_entry = hass.config_entries.async_get_entry(entry_reg.config_entry_id)
    coordinator: SmarteBewaesserungCoordinator | None = getattr(config_entry, "runtime_data", None)
    zone = coordinator.zone_for_subentry(entry_reg.config_subentry_id) if coordinator else None
    if coordinator is None or zone is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_zone", translation_placeholders={"entity": entity_id}
        )
    return coordinator, zone


@callback
def _register_services(hass: HomeAssistant) -> None:
    async def recalculate(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_refresh()

    async def record_irrigation(call: ServiceCall) -> None:
        coordinator, zone = _resolve_zone(hass, call.data[ATTR_ENTITY_ID])
        liters = call.data.get(ATTR_LITERS)
        minutes = call.data.get(ATTR_MINUTES)
        if liters is None and minutes is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="liters_or_minutes")
        if minutes is None:
            minutes = liters / zone.params.throughput_lpm
        measured = liters is not None
        if liters is None:
            liters = minutes * zone.params.throughput_lpm
        end = dt_util.utcnow()
        run = RunRecord(
            start=end - timedelta(minutes=minutes),
            end=end,
            minutes=minutes,
            liters=liters,
            measured=measured,
        )
        coordinator.record_run(zone, run)
        coordinator.refresh_now()

    async def set_depletion(call: ServiceCall) -> None:
        if ATTR_DEPLETION_MM not in call.data and ATTR_SOIL_WATER_PCT not in call.data:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="depletion_or_pct")
        # Mehrere Zonen auf einmal möglich, z. B. „Konten auf 0“ im Dashboard.
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, zone = _resolve_zone(hass, entity_id)
            if ATTR_DEPLETION_MM in call.data:
                value = call.data[ATTR_DEPLETION_MM]
            else:
                value = (1 - call.data[ATTR_SOIL_WATER_PCT] / 100) * zone.params.taw_mm
            coordinator.set_depletion(zone, value)

    async def calibrate_soil_sensor(call: ServiceCall) -> dict:
        coordinator, zone = _resolve_zone(hass, call.data[ATTR_ENTITY_ID])
        try:
            value = coordinator.calibrate_soil(zone, call.data[ATTR_POINT])
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_soil_value") from err
        return {"point": call.data[ATTR_POINT], "moisture_pct": value}

    async def set_winterized(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            entry.runtime_data.set_winterized(call.data[ATTR_WINTERIZED])

    async def weekly_report(call: ServiceCall) -> dict:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        return entries[0].runtime_data.weekly_report()

    hass.services.async_register(DOMAIN, SERVICE_RECALCULATE, recalculate)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_WINTERIZED,
        set_winterized,
        schema=vol.Schema({vol.Required(ATTR_WINTERIZED): cv.boolean}),
    )
    hass.services.async_register(DOMAIN, SERVICE_WEEKLY_REPORT, weekly_report, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECORD_IRRIGATION,
        record_irrigation,
        schema=vol.Schema(
            {
                **ZONE_TARGET,
                vol.Optional(ATTR_LITERS): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(ATTR_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DEPLETION,
        set_depletion,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
                vol.Exclusive(ATTR_DEPLETION_MM, "value"): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Exclusive(ATTR_SOIL_WATER_PCT, "value"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_SOIL_SENSOR,
        calibrate_soil_sensor,
        schema=vol.Schema({**ZONE_TARGET, vol.Required(ATTR_POINT): vol.In(["dry", "wet"])}),
        supports_response=SupportsResponse.OPTIONAL,
    )
