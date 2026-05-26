"""Constants for the Sector Alarm integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sector_alarm"
MANUFACTURER: Final = "Sector Alarm"

CONF_PANEL_ID: Final = "panel_id"
CONF_PIN: Final = "pin"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)
MIN_SCAN_INTERVAL_SECONDS: Final = 30
MAX_SCAN_INTERVAL_SECONDS: Final = 600

API_BASE_URL: Final = "https://mypagesapi.sectoralarm.net"
API_VERSION_HEADER: Final = "5"
USER_AGENT: Final = "ha-sector-alarm/0.1.0"
