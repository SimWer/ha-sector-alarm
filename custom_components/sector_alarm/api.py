"""Async HTTP client for the Sector Alarm My Pages API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import aiohttp
from aiohttp import ClientResponseError, ClientSession

from homeassistant.components.alarm_control_panel import AlarmControlPanelState

from .const import API_BASE_URL, API_VERSION_HEADER, USER_AGENT
from .models import (
    ContactSensor,
    LockInfo,
    PanelInfo,
    SectorData,
    TemperatureSensor,
)

_LOGGER = logging.getLogger(__name__)

_LOGIN_PATH = "/api/Login/Login"
_PANEL_PATH = "/api/Panel/GetPanel"
_ARM_PATH = "/api/Panel/ArmPanel"
_DISARM_PATH = "/api/Panel/Disarm"
_TEMPERATURES_PATH = "/api/Housecheck/GetTemperatures"
_DOORS_WINDOWS_PATH = "/api/Housecheck/Doorsandwindows"
_LOCKS_GET_PATH = "/api/Locks/GetLocks"
_LOCKS_LOCK_PATH = "/api/Locks/Lock"
_LOCKS_UNLOCK_PATH = "/api/Locks/Unlock"

_PANEL_STATE_MAP: dict[str, AlarmControlPanelState] = {
    "armed": AlarmControlPanelState.ARMED_AWAY,
    "armedaway": AlarmControlPanelState.ARMED_AWAY,
    "total": AlarmControlPanelState.ARMED_AWAY,
    "totalarmed": AlarmControlPanelState.ARMED_AWAY,
    "partialarmed": AlarmControlPanelState.ARMED_HOME,
    "partial": AlarmControlPanelState.ARMED_HOME,
    "armedhome": AlarmControlPanelState.ARMED_HOME,
    "disarmed": AlarmControlPanelState.DISARMED,
}


class SectorApiError(Exception):
    """Generic error talking to Sector Alarm."""


class SectorAuthError(SectorApiError):
    """Authentication failed (bad credentials or expired session)."""


class SectorRateLimitError(SectorApiError):
    """The API is rate-limiting us."""


def _normalize_panel_state(raw: Any) -> AlarmControlPanelState | None:
    if raw is None:
        return None
    key = str(raw).strip().lower().replace(" ", "").replace("_", "")
    return _PANEL_STATE_MAP.get(key)


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
        self._login_lock = asyncio.Lock()

    @property
    def panel_id(self) -> str:
        return self._panel_id

    @property
    def email(self) -> str:
        return self._email

    def update_credentials(self, password: str, pin: str | None) -> None:
        self._password = password
        if pin is not None:
            self._pin = pin
        self._cookie = None

    def _headers(self) -> dict[str, str]:
        return {
            "API-Version": API_VERSION_HEADER,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    async def login(self) -> None:
        """Authenticate and cache the session cookie."""
        async with self._login_lock:
            payload = {"UserId": self._email, "Password": self._password}
            try:
                async with self._session.post(
                    f"{API_BASE_URL}{_LOGIN_PATH}",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (401, 403):
                        raise SectorAuthError(
                            f"Login rejected by Sector ({resp.status})"
                        )
                    if resp.status == 429:
                        raise SectorRateLimitError("Rate-limited on login")
                    resp.raise_for_status()
                    # The cookie jar on the shared session might be filtered by
                    # the user's `unsafe` setting. We capture the Set-Cookie
                    # value defensively into our own dict so we don't rely on
                    # the session-wide jar.
                    cookies = {}
                    for c in resp.cookies.values():
                        cookies[c.key] = c.value
                    if not cookies:
                        # Some deployments return the token in the JSON body.
                        body = await resp.json(content_type=None)
                        if isinstance(body, dict) and body.get("AuthorizationToken"):
                            cookies["AuthorizationToken"] = body["AuthorizationToken"]
                    if not cookies:
                        raise SectorAuthError(
                            "Login succeeded but no session cookie/token returned"
                        )
                    self._cookie = cookies
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
        if self._cookie is None:
            await self.login()

        try:
            async with self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                json=json_body,
                params=params,
                headers=self._headers(),
                cookies=self._cookie,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (401, 403):
                    if retry_on_auth:
                        _LOGGER.debug("Session expired (%s) — re-login", resp.status)
                        self._cookie = None
                        await self.login()
                        return await self._request(
                            method,
                            path,
                            json_body=json_body,
                            params=params,
                            retry_on_auth=False,
                        )
                    raise SectorAuthError(
                        f"Sector returned {resp.status} after re-auth"
                    )
                if resp.status == 429:
                    raise SectorRateLimitError(f"{method} {path} rate-limited")
                resp.raise_for_status()
                if resp.status == 204 or resp.content_length == 0:
                    return None
                return await resp.json(content_type=None)
        except ClientResponseError as err:
            raise SectorApiError(f"HTTP {err.status} on {path}: {err.message}") from err
        except asyncio.TimeoutError as err:
            raise SectorApiError(f"Timeout on {path}") from err

    async def get_panel(self) -> PanelInfo:
        data = await self._request(
            "POST", _PANEL_PATH, json_body={"PanelId": self._panel_id}
        )
        if not isinstance(data, dict):
            raise SectorApiError("Unexpected response shape from GetPanel")
        return PanelInfo(
            panel_id=self._panel_id,
            display_name=str(
                data.get("PanelDisplayName") or data.get("DisplayName") or "Sector"
            ),
            state=_normalize_panel_state(data.get("Status") or data.get("ArmedStatus")),
        )

    async def get_temperatures(self) -> list[TemperatureSensor]:
        data = await self._request(
            "GET", _TEMPERATURES_PATH, params={"panelId": self._panel_id}
        )
        return [self._parse_temperature(item) for item in (data or [])]

    @staticmethod
    def _parse_temperature(item: dict[str, Any]) -> TemperatureSensor:
        raw = item.get("Temperature")
        try:
            temperature = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            temperature = None
        return TemperatureSensor(
            serial=str(item.get("SerialNo") or item.get("Serial") or item.get("Id")),
            name=str(item.get("Label") or item.get("Name") or "Temperature"),
            temperature=temperature,
        )

    async def get_doors_windows(self) -> list[ContactSensor]:
        data = await self._request(
            "GET", _DOORS_WINDOWS_PATH, params={"panelId": self._panel_id}
        )
        return [self._parse_contact(item) for item in (data or [])]

    @staticmethod
    def _parse_contact(item: dict[str, Any]) -> ContactSensor:
        closed = _coerce_bool(item.get("Closed"))
        return ContactSensor(
            serial=str(item.get("SerialNo") or item.get("Serial") or item.get("Id")),
            name=str(item.get("Label") or item.get("Name") or "Contact"),
            closed=bool(closed) if closed is not None else True,
            type=str(item.get("Type") or "Door"),
            low_battery=bool(item.get("LowBattery") or False),
        )

    async def get_locks(self) -> list[LockInfo]:
        data = await self._request(
            "GET", _LOCKS_GET_PATH, params={"panelId": self._panel_id}
        )
        return [self._parse_lock(item) for item in (data or [])]

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
        """Fetch panel, contacts, temperatures and locks concurrently."""
        panel, temps, contacts, locks = await asyncio.gather(
            self.get_panel(),
            self.get_temperatures(),
            self.get_doors_windows(),
            self.get_locks(),
        )
        return SectorData(
            panel=panel, temperatures=temps, contacts=contacts, locks=locks
        )

    async def arm(self, mode: Literal["away", "home"]) -> None:
        cmd = "Total" if mode == "away" else "Partial"
        await self._request(
            "POST",
            _ARM_PATH,
            json_body={
                "ArmCmd": cmd,
                "PanelCode": self._pin,
                "Id": self._panel_id,
            },
        )

    async def disarm(self) -> None:
        await self._request(
            "POST",
            _DISARM_PATH,
            json_body={"PanelCode": self._pin, "Id": self._panel_id},
        )

    async def lock_door(self, serial: str) -> None:
        await self._request(
            "POST",
            _LOCKS_LOCK_PATH,
            json_body={
                "LockSerial": serial,
                "PanelCode": self._pin,
                "Id": self._panel_id,
            },
        )

    async def unlock_door(self, serial: str) -> None:
        await self._request(
            "POST",
            _LOCKS_UNLOCK_PATH,
            json_body={
                "LockSerial": serial,
                "PanelCode": self._pin,
                "Id": self._panel_id,
            },
        )
