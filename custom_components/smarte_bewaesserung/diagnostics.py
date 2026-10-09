"""Diagnose-Download."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import SmarteBewaesserungConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmarteBewaesserungConfigEntry
) -> dict[str, Any]:
    """Konfiguration, gespeicherte Wasserkonten und Wetterdaten."""
    return {
        "options": dict(entry.options),
        "zones": {sub.title: dict(sub.data) for sub in entry.subentries.values()},
        **entry.runtime_data.diagnostics(),
    }
