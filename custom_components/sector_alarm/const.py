"""Constants for the Sector Alarm integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sector_alarm"
MANUFACTURER: Final = "Sector Alarm"

CONF_PANEL_ID: Final = "panel_id"
CONF_PIN: Final = "pin"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Sector allows 60 requests per clock hour, account-wide, resetting at :00.
# Measured 2026-09-19 by instrumenting the client: a clean run from 09:00:25
# was refused at 09:10:21 with "after 60 requests". The 429 carries
# x-envoy-ratelimited and nothing else — no Retry-After, no quota headers —
# so the budget has to be respected by construction, not discovered at runtime.
API_HOURLY_BUDGET: Final = 60

# Only GetPanelStatus changes minute to minute. The housecheck trio
# (temperatures, humidity, doors/windows) moves slowly, and GetPanel is static
# inventory. Polling all five every minute cost 300 requests/hour — 5x over.
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=300)
MIN_SCAN_INTERVAL_SECONDS: Final = 120
MAX_SCAN_INTERVAL_SECONDS: Final = 3600

AUX_REFRESH_SECONDS: Final = 1200          # housecheck trio: 3 calls
PANEL_INFO_REFRESH_SECONDS: Final = 3600   # inventory + display name: 1 call


def estimated_hourly_requests(scan_interval_seconds: int) -> int:
    """Requests per hour for a given status interval, given the slower tiers.

    Used to refuse or warn about a configuration that cannot fit the budget,
    rather than letting the user discover it as an hour of downtime.
    """
    status = 3600 // max(scan_interval_seconds, 1)
    aux = (3600 // AUX_REFRESH_SECONDS) * 3
    info = 3600 // PANEL_INFO_REFRESH_SECONDS
    return status + aux + info

API_BASE_URL: Final = "https://mypagesapi.sectoralarm.net"
API_VERSION_HEADER: Final = "5"
USER_AGENT: Final = "ha-sector-alarm/0.1.0"
