"""The Sector Alarm integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SectorAlarmClient
from .const import (
    CONF_PANEL_ID,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import SectorDataUpdateCoordinator
from .models import SectorConfigEntry, SectorRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: SectorConfigEntry) -> bool:
    """Set up Sector Alarm from a config entry."""
    session = async_get_clientsession(hass)
    client = SectorAlarmClient(
        session=session,
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        panel_id=entry.data[CONF_PANEL_ID],
        pin=entry.data.get(CONF_PIN),
    )

    interval_seconds = entry.options.get(
        CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
    )
    coordinator = SectorDataUpdateCoordinator(
        hass, client, timedelta(seconds=interval_seconds)
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SectorRuntimeData(client=client, coordinator=coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SectorConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: SectorConfigEntry
) -> None:
    """Reload the entry when options change (e.g. scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
