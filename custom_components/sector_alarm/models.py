"""Typed dataclasses for Sector Alarm data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import SectorAlarmClient
    from .coordinator import SectorDataUpdateCoordinator


@dataclass(slots=True)
class PanelInfo:
    panel_id: str
    display_name: str
    state: AlarmControlPanelState | None


@dataclass(slots=True)
class ContactSensor:
    serial: str
    name: str
    closed: bool
    type: str  # "Door" or "Window"
    low_battery: bool = False


@dataclass(slots=True)
class TemperatureSensor:
    serial: str
    name: str
    temperature: float | None


@dataclass(slots=True)
class HumiditySensor:
    serial: str
    name: str
    humidity: float | None


@dataclass(slots=True)
class LockInfo:
    serial: str
    name: str
    locked: bool | None  # None when status is unknown
    low_battery: bool = False


@dataclass(slots=True)
class SectorData:
    panel: PanelInfo
    contacts: list[ContactSensor] = field(default_factory=list)
    temperatures: list[TemperatureSensor] = field(default_factory=list)
    humidities: list[HumiditySensor] = field(default_factory=list)
    locks: list[LockInfo] = field(default_factory=list)


@dataclass(slots=True)
class SectorRuntimeData:
    client: "SectorAlarmClient"
    coordinator: "SectorDataUpdateCoordinator"


SectorConfigEntry = ConfigEntry[SectorRuntimeData]
