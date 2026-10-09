"""Binärsensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmarteBewaesserungConfigEntry
from .coordinator import Snapshot, ZoneSnapshot
from .entity import GlobalEntity, ZoneEntity


@dataclass(frozen=True, kw_only=True)
class GlobalBinaryDescription(BinarySensorEntityDescription):
    """Beschreibung eines globalen Binärsensors."""

    is_on_fn: Callable[[Snapshot, Any], bool]


@dataclass(frozen=True, kw_only=True)
class ZoneBinaryDescription(BinarySensorEntityDescription):
    """Beschreibung eines Zonen-Binärsensors."""

    is_on_fn: Callable[[ZoneSnapshot], bool]
    attrs_fn: Callable[[ZoneSnapshot], dict[str, Any]] | None = None
    requires_valve: bool = False


GLOBAL_BINARY: tuple[GlobalBinaryDescription, ...] = (
    GlobalBinaryDescription(
        key="frost",
        device_class=BinarySensorDeviceClass.COLD,
        is_on_fn=lambda s, _t: s.frost,
    ),
    GlobalBinaryDescription(
        key="rain_block",
        device_class=BinarySensorDeviceClass.MOISTURE,
        is_on_fn=lambda s, _t: s.rain_blocked,
    ),
)

ZONE_BINARY: tuple[ZoneBinaryDescription, ...] = (
    ZoneBinaryDescription(
        key="irrigation_recommended",
        is_on_fn=lambda z: z.recommendation.water,
        attrs_fn=lambda z: {
            "minutes": z.recommendation.minutes,
            "liters": z.recommendation.liters,
            "reason": z.recommendation.reason,
        },
    ),
    ZoneBinaryDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda z: bool(z.faults),
        attrs_fn=lambda z: {"faults": z.faults},
    ),
    ZoneBinaryDescription(
        key="watering",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda z: z.watering,
        requires_valve=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmarteBewaesserungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Binärsensoren anlegen."""
    coordinator = entry.runtime_data
    async_add_entities(GlobalBinary(coordinator, d) for d in GLOBAL_BINARY)
    for subentry_id, zone in coordinator.zones.items():
        async_add_entities(
            (
                ZoneBinary(coordinator, subentry_id, d)
                for d in ZONE_BINARY
                if not d.requires_valve or zone.tracker is not None
            ),
            config_subentry_id=subentry_id,
        )


class GlobalBinary(GlobalEntity, BinarySensorEntity):
    """Globaler Binärsensor."""

    entity_description: GlobalBinaryDescription

    def __init__(self, coordinator, description: GlobalBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data, self.coordinator.thresholds)


class ZoneBinary(ZoneEntity, BinarySensorEntity):
    """Binärsensor einer Zone."""

    entity_description: ZoneBinaryDescription

    def __init__(self, coordinator, subentry_id: str, description: ZoneBinaryDescription) -> None:
        super().__init__(coordinator, subentry_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        zone = self.zone_data
        return self.entity_description.is_on_fn(zone) if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        zone = self.zone_data
        return fn(zone) if fn and zone else None
