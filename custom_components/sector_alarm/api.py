"""Async HTTP client for the Sector Alarm My Pages API."""

from __future__ import annotations

import asyncio
from time import monotonic
import json as _json
import logging
from typing import Any, Literal

import aiohttp
from aiohttp import ClientResponseError, ClientSession

from homeassistant.components.alarm_control_panel import AlarmControlPanelState

from .const import (
    API_BASE_URL,
    API_VERSION_HEADER,
    AUX_REFRESH_SECONDS,
    PANEL_INFO_REFRESH_SECONDS,
    USER_AGENT,
)
from .models import (
    ContactSensor,
    HumiditySensor,
    LockInfo,
    PanelInfo,
    SectorData,
    TemperatureSensor,
)

_LOGGER = logging.getLogger(__name__)

# Endpoints — captured from My Pages traffic for "new SAS" (Auth0-backed)
# customers. All authenticated with `Authorization: Bearer <JWT>`.
_LOGIN_PATH = "/api/Login/Login"
_PANEL_INFO_PATH = "/api/Panel/GetPanel"            # GET ?panelId=...
_PANEL_STATUS_PATH = "/api/Panel/GetPanelStatus"    # GET ?panelId=...
_TEMPERATURES_PATH = "/api/v2/housecheck/temperatures"   # POST {panelId}
_DOORS_WINDOWS_PATH = "/api/housecheck/doorsandwindows"  # POST {panelId}
_HUMIDITY_PATH_TPL = "/api/housecheck/panels/{panel_id}/humidity"  # GET (panel in path)
_LOCKS_GET_PATH = "/api/Locks/GetLocks"             # GET ?panelId=...  (legacy, may differ)
_ARM_PATH = "/api/Panel/Arm"                        # POST — shape unconfirmed for new SAS
_DISARM_PATH = "/api/Panel/Disarm"                  # POST — shape unconfirmed for new SAS
_LOCKS_LOCK_PATH = "/api/Locks/Lock"                # POST — unconfirmed
_LOCKS_UNLOCK_PATH = "/api/Locks/Unlock"            # POST — unconfirmed

# Integer status values returned by GetPanelStatus.Status.
# Best-guess mapping; iterate when we see other values in the wild.
_PANEL_STATE_INT_MAP: dict[int, AlarmControlPanelState] = {
    1: AlarmControlPanelState.DISARMED,
    2: AlarmControlPanelState.ARMED_HOME,
    3: AlarmControlPanelState.ARMED_AWAY,
}


class SectorApiError(Exception):
    """Generic error talking to Sector Alarm."""


class SectorAuthError(SectorApiError):
    """Authentication failed (bad credentials or expired session)."""


class SectorRateLimitError(SectorApiError):
    """The API is rate-limiting us.

    Carries whatever the server disclosed about the limit, so the cause can be
    measured rather than inferred from how long we lasted.
    """

    def __init__(
        self,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        requests_made: int | None = None,
    ) -> None:
        super().__init__(message)
        self.headers = headers or {}
        self.body = body
        self.requests_made = requests_made

    @property
    def retry_after(self) -> int | None:
        """Seconds the server asked us to wait, when it says so."""
        value = self.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None


def _normalize_panel_state(raw: Any) -> AlarmControlPanelState | None:
    """Map Sector's Status field to an HA state. Accepts int or string."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
        return _PANEL_STATE_INT_MAP.get(int(raw))
    # legacy string path (older accounts)
    key = str(raw).strip().lower().replace(" ", "").replace("_", "")
    return {
        "disarmed": AlarmControlPanelState.DISARMED,
        "armed": AlarmControlPanelState.ARMED_AWAY,
        "armedaway": AlarmControlPanelState.ARMED_AWAY,
        "total": AlarmControlPanelState.ARMED_AWAY,
        "totalarmed": AlarmControlPanelState.ARMED_AWAY,
        "partial": AlarmControlPanelState.ARMED_HOME,
        "partialarmed": AlarmControlPanelState.ARMED_HOME,
        "armedhome": AlarmControlPanelState.ARMED_HOME,
    }.get(key)


def _housecheck_floors(data: Any) -> list[dict[str, Any]]:
    """Normalize a housecheck response into a flat list of floor dicts.

    Sector returns either a bare list `[floor1, floor2]` or a dict wrapping
    one (e.g. `{"Floors": [floor1, floor2]}` — the key name varies by
    endpoint version). Anything else is treated as empty.
    """
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [f for f in v if isinstance(f, dict)]
    return []


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "closed", "locked", "lock"):
            return True
        if v in ("false", "0", "no", "open", "unlocked", "unlock"):
            return False
    return None


class SectorAlarmClient:
    """Minimal async client for the Sector Alarm My Pages REST API."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
        panel_id: str,
        pin: str | None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._panel_id = str(panel_id)
        self._pin = pin or ""
        self._cookie: dict[str, str] | None = None
        self._bearer: str | None = None
        self._login_lock = asyncio.Lock()
        # Count every HTTP call we make, so a 429 can report exactly how many
        # requests preceded it instead of us inferring it from uptime.
        self._request_count = 0
        # --- tiered polling cache ---
        # Each tier keeps its last good payload so a slow tier's turn coming
        # round, or failing, never blanks entities that were fine a second ago.
        self._cache_info: dict[str, Any] | None = None
        self._cache_temps: list[TemperatureSensor] = []
        self._cache_humidities: list[HumiditySensor] = []
        self._cache_contacts: list[ContactSensor] = []
        self._last_info: float = 0.0
        self._last_aux: float = 0.0

    @property
    def panel_id(self) -> str:
        return self._panel_id

    @property
    def email(self) -> str:
        return self._email

    @property
    def has_pin(self) -> bool:
        return bool(self._pin)

    def update_credentials(self, password: str, pin: str | None) -> None:
        self._password = password
        if pin is not None:
            self._pin = pin
        self._cookie = None
        self._bearer = None

    def _headers(self, *, authed: bool = False) -> dict[str, str]:
        headers = {
            "API-Version": API_VERSION_HEADER,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if authed and self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        return headers

    async def login(self) -> None:
        """POST /api/Login/Login → JWT in `AuthorizationToken` body field."""
        async with self._login_lock:
            payload = {"UserId": self._email, "Password": self._password}
            self._request_count += 1
            try:
                async with self._session.post(
                    f"{API_BASE_URL}{_LOGIN_PATH}",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    body_text = await resp.text()
                    if resp.status in (401, 403):
                        _LOGGER.warning(
                            "Sector login rejected (%s): %s",
                            resp.status, body_text[:200],
                        )
                        raise SectorAuthError(
                            f"Login rejected by Sector ({resp.status})"
                        )
                    if resp.status == 429:
                        interesting = {
                            k: v for k, v in resp.headers.items()
                            if "rate" in k.lower() or "retry" in k.lower()
                            or "limit" in k.lower() or "quota" in k.lower()
                        }
                        _LOGGER.warning(
                            "Sector rate-limited LOGIN after %s requests this "
                            "session. rate-limit headers=%s body=%s",
                            self._request_count, interesting or "(none)",
                            body_text[:300] or "(empty)",
                        )
                        raise SectorRateLimitError(
                            "Rate-limited on login",
                            headers=dict(resp.headers),
                            body=body_text,
                            requests_made=self._request_count,
                        )
                    if resp.status >= 400:
                        _LOGGER.warning(
                            "Sector login HTTP %s: %s", resp.status, body_text[:200],
                        )
                        resp.raise_for_status()

                    bearer: str | None = None
                    try:
                        body = _json.loads(body_text) if body_text else None
                    except ValueError:
                        body = None
                    if isinstance(body, dict):
                        bearer = (
                            body.get("AuthorizationToken")
                            or body.get("accessToken")
                            or body.get("access_token")
                        )
                    cookies = {c.key: c.value for c in resp.cookies.values()}
                    if not bearer and not cookies:
                        raise SectorAuthError(
                            "Login succeeded but no auth token / cookie returned"
                        )
                    self._bearer = bearer
                    self._cookie = cookies or None
                    _LOGGER.debug(
                        "Sector login OK (bearer=%s, cookies=%d)",
                        "yes" if bearer else "no", len(cookies),
                    )
            except ClientResponseError as err:
                raise SectorApiError(f"HTTP error on login: {err}") from err
            except asyncio.TimeoutError as err:
                raise SectorApiError("Timeout during login") from err

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_on_auth: bool = True,
    ) -> Any:
        if self._bearer is None and self._cookie is None:
            await self.login()

        self._request_count += 1
        try:
            async with self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                json=json_body,
                params=params,
                headers=self._headers(authed=True),
                cookies=self._cookie,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (401, 403):
                    if retry_on_auth:
                        body = await resp.text()
                        _LOGGER.debug(
                            "Sector %s %s -> %s; re-logging in. body=%s",
                            method, path, resp.status, body[:200],
                        )
                        self._cookie = None
                        self._bearer = None
                        await self.login()
                        return await self._request(
                            method, path,
                            json_body=json_body, params=params,
                            retry_on_auth=False,
                        )
                    body = await resp.text()
                    raise SectorAuthError(
                        f"Sector returned {resp.status} after re-auth: {body[:200]}"
                    )
                if resp.status == 429:
                    # Log everything the server disclosed. Sector does not
                    # document its quota, so these headers are the only way to
                    # know the real limit instead of inferring it from uptime.
                    body = await resp.text()
                    interesting = {
                        k: v for k, v in resp.headers.items()
                        if "rate" in k.lower() or "retry" in k.lower()
                        or "limit" in k.lower() or "quota" in k.lower()
                    }
                    _LOGGER.warning(
                        "Sector rate-limited %s %s after %s requests this session. "
                        "rate-limit headers=%s body=%s all-headers=%s",
                        method, path, self._request_count,
                        interesting or "(none)", body[:300] or "(empty)",
                        dict(resp.headers),
                    )
                    raise SectorRateLimitError(
                        f"{method} {path} rate-limited",
                        headers=dict(resp.headers),
                        body=body,
                        requests_made=self._request_count,
                    )
                if resp.status >= 400:
                    body = await resp.text()
                    _LOGGER.warning(
                        "Sector %s %s -> %s body=%s",
                        method, path, resp.status, body[:300],
                    )
                    resp.raise_for_status()
                if resp.status == 204 or resp.content_length == 0:
                    return None
                return await resp.json(content_type=None)
        except ClientResponseError as err:
            raise SectorApiError(f"HTTP {err.status} on {path}: {err.message}") from err
        except asyncio.TimeoutError as err:
            raise SectorApiError(f"Timeout on {path}") from err

    async def get_panel_info(self) -> dict[str, Any]:
        """GET /api/Panel/GetPanel?panelId=... — inventory + display name."""
        data = await self._request(
            "GET", _PANEL_INFO_PATH, params={"panelId": self._panel_id}
        )
        if not isinstance(data, dict):
            raise SectorApiError("Unexpected response shape from GetPanel")
        return data

    async def get_panel_status(self) -> dict[str, Any]:
        """GET /api/Panel/GetPanelStatus?panelId=... — live state."""
        data = await self._request(
            "GET", _PANEL_STATUS_PATH, params={"panelId": self._panel_id}
        )
        if not isinstance(data, dict):
            raise SectorApiError("Unexpected response shape from GetPanelStatus")
        return data

    async def get_panel(self, info: dict[str, Any] | None = None) -> PanelInfo:
        """Combine GetPanel + GetPanelStatus into a single PanelInfo."""
        if info is None:
            info = await self.get_panel_info()
        status = await self.get_panel_status()
        return PanelInfo(
            panel_id=self._panel_id,
            display_name=str(info.get("DisplayName") or "Sector"),
            state=_normalize_panel_state(status.get("Status")),
        )

    async def get_temperatures(self) -> list[TemperatureSensor]:
        """POST /api/v2/housecheck/temperatures.

        Returns a Floor → Places → Components tree. We flatten and add
        the place name so similar devices in different rooms get distinct
        labels.
        """
        try:
            data = await self._request(
                "POST",
                _TEMPERATURES_PATH,
                json_body={"panelId": self._panel_id},
            )
        except SectorApiError as err:
            _LOGGER.debug("v2 temperatures fetch failed (%s)", err)
            return []
        return [
            self._parse_temperature(component)
            for floor in _housecheck_floors(data)
            for place in (floor.get("Places") or [])
            if isinstance(place, dict)
            for component in (place.get("Components") or [])
            if isinstance(component, dict)
        ]

    @staticmethod
    def _parse_temperatures_from_panel(info: dict[str, Any]) -> list[TemperatureSensor]:
        """Read temperatures inline from GetPanel response."""
        return [
            SectorAlarmClient._parse_temperature(item)
            for item in (info.get("Temperatures") or [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _parse_temperature(item: dict[str, Any]) -> TemperatureSensor:
        # The legacy GetPanel response sometimes spells it "Temprature";
        # the v2 endpoint uses "Temperature".
        raw = item.get("Temperature")
        if raw in (None, ""):
            raw = item.get("Temprature")
        try:
            temperature = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            temperature = None
        return TemperatureSensor(
            serial=str(
                item.get("SerialNo")
                or item.get("Serial")
                or item.get("DeviceId")
                or item.get("Id")
            ),
            name=str(item.get("Label") or item.get("Name") or "Temperature"),
            temperature=temperature,
        )

    async def get_humidity(self) -> list[HumiditySensor]:
        """GET /api/housecheck/panels/{panel_id}/humidity.

        Same Section → Place → Component shape as temperatures, but the panel
        ID is in the path (not the body) and the request is a GET.
        """
        try:
            data = await self._request(
                "GET",
                _HUMIDITY_PATH_TPL.format(panel_id=self._panel_id),
            )
        except SectorApiError as err:
            _LOGGER.debug("humidity fetch failed (%s)", err)
            return []
        return [
            self._parse_humidity(component)
            for section in _housecheck_floors(data)
            for place in (section.get("Places") or [])
            if isinstance(place, dict)
            for component in (place.get("Components") or [])
            if isinstance(component, dict)
        ]

    @staticmethod
    def _parse_humidity(item: dict[str, Any]) -> HumiditySensor:
        raw = item.get("Humidity")
        try:
            humidity = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            humidity = None
        return HumiditySensor(
            serial=str(
                item.get("SerialNo")
                or item.get("Serial")
                or item.get("DeviceId")
                or item.get("Id")
            ),
            name=str(item.get("Label") or item.get("Name") or "Humidity"),
            humidity=humidity,
        )

    async def get_doors_windows(self) -> list[ContactSensor]:
        """POST /api/housecheck/doorsandwindows.

        Returns a Floor → Rooms → Devices tree.
        """
        try:
            data = await self._request(
                "POST",
                _DOORS_WINDOWS_PATH,
                json_body={"panelId": self._panel_id},
            )
        except SectorApiError as err:
            _LOGGER.debug("doorsandwindows fetch failed (%s)", err)
            return []
        return [
            self._parse_contact(device)
            for floor in _housecheck_floors(data)
            for room in (floor.get("Rooms") or [])
            if isinstance(room, dict)
            for device in (room.get("Devices") or [])
            if isinstance(device, dict)
        ]

    @staticmethod
    def _parse_contact(item: dict[str, Any]) -> ContactSensor:
        closed = _coerce_bool(item.get("Closed"))
        # In the new shape `Type` is an int; treat 2 as window, anything
        # else as a door (best guess until we see a window in the wild).
        raw_type = item.get("Type")
        if isinstance(raw_type, int):
            type_str = "Window" if raw_type == 2 else "Door"
        else:
            type_str = str(raw_type or "Door")
        return ContactSensor(
            serial=str(
                item.get("SerialString")
                or item.get("SerialNo")
                or item.get("Serial")
                or item.get("DeviceId")
                or item.get("Id")
            ),
            name=str(item.get("Label") or item.get("Name") or "Contact"),
            closed=bool(closed) if closed is not None else True,
            type=type_str,
            low_battery=bool(item.get("LowBattery") or False),
        )

    async def get_locks(self) -> list[LockInfo]:
        """Returns the locks declared on the panel info (no separate fetch
        needed — they appear in the GetPanel response's `Locks` array)."""
        # Endpoint shape for new SAS unclear; rely on GetPanel for inventory
        # and skip a dedicated locks fetch until we capture one.
        return []

    @staticmethod
    def _parse_lock(item: dict[str, Any]) -> LockInfo:
        status = item.get("Status")
        if isinstance(status, str):
            lowered = status.strip().lower()
            if lowered == "lock":
                locked: bool | None = True
            elif lowered == "unlock":
                locked = False
            else:
                locked = _coerce_bool(status)
        else:
            locked = _coerce_bool(status)
        return LockInfo(
            serial=str(item.get("Serial") or item.get("SerialNo") or item.get("Id")),
            name=str(item.get("Label") or item.get("Name") or "Lock"),
            locked=locked,
            low_battery=bool(item.get("BatteryLow") or False),
        )

    async def fetch_all(self) -> SectorData:
        """Fetch the alarm state every poll; refresh slower data on its own tier.

        Sector allows 60 requests per clock hour (see const.API_HOURLY_BUDGET).
        Fetching all five endpoints on every poll spent 300/hour and left the
        integration rate-limited for roughly fifty minutes of every hour. Only
        GetPanelStatus carries state that changes minute to minute, so it is
        the only call made every time:

            GetPanelStatus   every poll            state
            housecheck x3    AUX_REFRESH_SECONDS   temperatures/humidity/doors
            GetPanel         PANEL_INFO_REFRESH_S  inventory, names, locks

        At the 300s default that is 22 requests/hour with alarm state never
        more than five minutes stale.
        """
        now = monotonic()
        first_run = self._cache_info is None
        need_info = first_run or (now - self._last_info) >= PANEL_INFO_REFRESH_SECONDS
        need_aux = self._last_aux == 0.0 or (now - self._last_aux) >= AUX_REFRESH_SECONDS

        # Status is the only unconditional call.
        status = await self.get_panel_status()

        if need_info:
            try:
                self._cache_info = await self.get_panel_info()
                self._last_info = now
            except SectorApiError:
                # Inventory is static and we already have it; a failed refresh
                # must not take the alarm offline. Only the very first fetch,
                # which establishes the panel, is allowed to fail the poll.
                if first_run:
                    raise
                _LOGGER.debug("GetPanel refresh failed; keeping cached inventory")
        info = self._cache_info or {}

        if need_aux:
            live_temps, humidities, contacts = await asyncio.gather(
                self.get_temperatures(),
                self.get_humidity(),
                self.get_doors_windows(),
            )
            # These getters return [] both when empty and when they failed, so
            # only replace a cache that has something to replace it with.
            if live_temps or not self._cache_temps:
                self._cache_temps = live_temps
            if humidities or not self._cache_humidities:
                self._cache_humidities = humidities
            if contacts or not self._cache_contacts:
                self._cache_contacts = contacts
            self._last_aux = now

        panel = PanelInfo(
            panel_id=self._panel_id,
            display_name=str(info.get("DisplayName") or "Sector"),
            state=_normalize_panel_state(status.get("Status")),
        )
        # Prefer live temperature values; fall back to the inventory from
        # GetPanel (which is what the user has if the v2 endpoint 404s).
        temps = self._cache_temps or self._parse_temperatures_from_panel(info)
        locks = [
            self._parse_lock(item)
            for item in (info.get("Locks") or [])
            if isinstance(item, dict)
        ]
        return SectorData(
            panel=panel,
            temperatures=temps,
            humidities=self._cache_humidities,
            contacts=self._cache_contacts,
            locks=locks,
        )

    async def _command(
        self, path: str, action: str, code: str | None, **extra: Any
    ) -> None:
        """POST a panel command with the standard PanelId+PanelCode envelope.

        Sector's mutation endpoints all share the same body shape:
        `{"PanelId": ..., "PanelCode": <pin>, ...action-specific fields}`.
        The "User Code" wording in error messages is just the customer-facing
        label; the JSON field is `PanelCode` (see `invalid_panel_code__empty`).
        """
        pin = code or self._pin
        if not pin:
            raise SectorApiError(f"{action} requires a PIN")
        await self._request(
            "POST",
            path,
            json_body={
                "PanelId": self._panel_id,
                "PanelCode": pin,
                **extra,
            },
        )

    async def arm(self, mode: Literal["away", "home"], code: str | None = None) -> None:
        await self._command(
            _ARM_PATH, "Arm", code,
            ArmCmd="Total" if mode == "away" else "Partial",
        )

    async def disarm(self, code: str | None = None) -> None:
        await self._command(_DISARM_PATH, "Disarm", code)

    async def lock_door(self, serial: str, code: str | None = None) -> None:
        await self._command(_LOCKS_LOCK_PATH, "Lock", code, LockSerial=serial)

    async def unlock_door(self, serial: str, code: str | None = None) -> None:
        await self._command(_LOCKS_UNLOCK_PATH, "Unlock", code, LockSerial=serial)
