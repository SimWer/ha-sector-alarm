"""Data update coordinator for Sector Alarm."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SectorAlarmClient, SectorApiError, SectorAuthError
from .const import DOMAIN
from .models import SectorData

_LOGGER = logging.getLogger(__name__)


class SectorDataUpdateCoordinator(DataUpdateCoordinator[SectorData]):
    """Polls Sector Alarm and exposes the unified SectorData snapshot."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SectorAlarmClient,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{client.panel_id}",
            update_interval=update_interval,
        )
        self.client = client

    async def _async_update_data(self) -> SectorData:
        try:
            return await self.client.fetch_all()
        except SectorAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SectorApiError as err:
            raise UpdateFailed(str(err)) from err
