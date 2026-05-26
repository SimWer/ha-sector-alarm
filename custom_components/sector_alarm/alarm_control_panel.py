"""Alarm control panel platform for Sector Alarm."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import SectorApiError
from .coordinator import SectorDataUpdateCoordinator
from .entity import SectorBaseEntity
from .models import SectorConfigEntry

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SectorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([SectorAlarmPanel(coordinator)])


class SectorAlarmPanel(SectorBaseEntity, AlarmControlPanelEntity):
    """Sector Alarm panel — arm away, arm home, disarm."""

    _attr_name = None  # use the device name as the entity name
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = False

    def __init__(self, coordinator: SectorDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.client.panel_id}_panel"

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        data = self.coordinator.data
        return data.panel.state if data else None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._send_arm("away")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._send_arm("home")

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        try:
            await self.coordinator.client.disarm()
        except SectorApiError as err:
            raise HomeAssistantError(f"Disarm failed: {err}") from err
        await self.coordinator.async_request_refresh()

    async def _send_arm(self, mode: str) -> None:
        try:
            await self.coordinator.client.arm(mode)  # type: ignore[arg-type]
        except SectorApiError as err:
            raise HomeAssistantError(f"Arm {mode} failed: {err}") from err
        await self.coordinator.async_request_refresh()
