"""Basisklassen der Entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmarteBewaesserungCoordinator, ZoneSnapshot


class GlobalEntity(CoordinatorEntity[SmarteBewaesserungCoordinator]):
    """Entity am Gerät „Smarte Bewässerung“ (Wetter, Sperren)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmarteBewaesserungCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Smarte Bewässerung",
            model="Wetter & Wasserbilanz",
            entry_type=DeviceEntryType.SERVICE,
        )


class ZoneEntity(CoordinatorEntity[SmarteBewaesserungCoordinator]):
    """Entity an einer Zone (ein Gerät pro Zone)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmarteBewaesserungCoordinator, subentry_id: str, key: str) -> None:
        super().__init__(coordinator)
        zone = coordinator.zones[subentry_id]
        self.subentry_id = subentry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=zone.name,
            manufacturer="Smarte Bewässerung",
            model="Zone",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def zone_data(self) -> ZoneSnapshot | None:
        """Aktueller Zustand dieser Zone."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.zones.get(self.subentry_id)

    @property
    def available(self) -> bool:
        return super().available and self.zone_data is not None
