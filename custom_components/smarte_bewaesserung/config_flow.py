"""Config Flow: Hauptentry (Wetter/Sperren) und Zonen als Subentries."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

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
    IRRIGATION_TYPES,
    SOIL_TYPES,
    SUBENTRY_ZONE,
)


def _number(min_: float, max_: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=min_, max=max_, step=step, mode=selector.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _entity(domains: list[str], device_class: str | list[str] | None = None) -> selector.EntitySelector:
    config: dict[str, Any] = {"domain": domains}
    if device_class:
        config["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**config))


def _select(options: list[str], key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, translation_key=key, mode=selector.SelectSelectorMode.DROPDOWN)
    )


SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_RAIN_SENSOR): _entity(["sensor", "binary_sensor"]),
        vol.Required(CONF_RAIN_SKIP_MM, default=DEFAULT_RAIN_SKIP_MM): _number(0, 50, 0.5, "mm"),
        vol.Required(CONF_FROST_C, default=DEFAULT_FROST_C): _number(-10, 15, 0.5, "°C"),
        vol.Required(CONF_WIND_KMH, default=DEFAULT_WIND_KMH): _number(0, 150, 1, "km/h"),
        vol.Required(CONF_SOIL_SENSOR_WEIGHT, default=DEFAULT_SOIL_SENSOR_WEIGHT): _number(0, 1, 0.1),
    }
)

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        vol.Required(CONF_AREA): _number(0.5, 5000, 0.5, "m²"),
        vol.Required(CONF_THROUGHPUT): _number(0.1, 200, 0.1, "L/min"),
        vol.Required(CONF_IRRIGATION_TYPE, default="sprinkler"): _select(IRRIGATION_TYPES, "irrigation_type"),
        vol.Required(CONF_KC, default=0.8): _number(0.1, 2.0, 0.05),
        vol.Required(CONF_SOIL_TYPE, default="loam"): _select(SOIL_TYPES, "soil_type"),
        vol.Required(CONF_ROOT_DEPTH, default=20): _number(5, 150, 5, "cm"),
        vol.Required(CONF_DEPLETION_FRACTION, default=0.5): _number(0.1, 0.9, 0.05),
        vol.Required(CONF_MIN_DURATION, default=3): _number(0, 60, 1, "min"),
        vol.Required(CONF_MAX_DURATION, default=30): _number(1, 240, 1, "min"),
        vol.Optional(CONF_VALVE): _entity(["switch", "valve"]),
        vol.Optional(CONF_VOLUME_SENSOR): _entity(["sensor"]),
        vol.Optional(CONF_FLOW_SENSOR): _entity(["sensor"]),
        vol.Optional(CONF_SOIL_MOISTURE_SENSOR): _entity(["sensor"]),
        vol.Required(CONF_SOIL_DRY_PCT, default=DEFAULT_SOIL_DRY_PCT): _number(0, 100, 1, "%"),
        vol.Required(CONF_SOIL_WET_PCT, default=DEFAULT_SOIL_WET_PCT): _number(0, 100, 1, "%"),
        vol.Optional(CONF_COMPARE_ENTITY): _entity(["sensor"]),
    }
)


def _validate_zone(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if data[CONF_MIN_DURATION] > data[CONF_MAX_DURATION]:
        errors[CONF_MIN_DURATION] = "min_above_max"
    if data[CONF_SOIL_DRY_PCT] >= data[CONF_SOIL_WET_PCT]:
        errors[CONF_SOIL_WET_PCT] = "wet_not_above_dry"
    return errors


class SmarteBewaesserungConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtung der Integration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Smarte Bewässerung", data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=SETTINGS_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SmarteBewaesserungOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ZONE: ZoneSubentryFlow}


class SmarteBewaesserungOptionsFlow(OptionsFlow):
    """Sperrschwellen und Regensensor ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(SETTINGS_SCHEMA, self.config_entry.options),
        )


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Zone anlegen oder ändern."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_zone(user_input)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(ZONE_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_zone(user_input)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(ZONE_SCHEMA, user_input or subentry.data),
            errors=errors,
        )
