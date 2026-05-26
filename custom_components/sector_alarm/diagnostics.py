"""Diagnostics support for Sector Alarm."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_PIN
from .models import SectorConfigEntry

_REDACT = {CONF_PASSWORD, CONF_PIN, CONF_EMAIL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SectorConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), _REDACT),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "coordinator_data": {
            "panel": asdict(data.panel) if data else None,
            "contacts": [asdict(c) for c in data.contacts] if data else [],
            "temperatures": [asdict(t) for t in data.temperatures] if data else [],
            "locks": [asdict(lk) for lk in data.locks] if data else [],
        },
    }
