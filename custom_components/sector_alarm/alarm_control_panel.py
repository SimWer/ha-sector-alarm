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
    """Sector Alarm panel — arm away, arm home, disarm.

    PIN handling: if a code is passed from the HA UI dialog or service call,
    use it. Otherwise fall back to the PIN stored on the config entry (if
    any). Either path works; `code_arm_required` is only True when no PIN
    is stored, so the UI prompts.
    """

    _attr_name = None
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_format = CodeFormat.NUMBER

    def __init__(self, coordinator: SectorDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.client.panel_id}_panel"

    @property
    def code_arm_required(self) -> bool:
        # Only prompt for code in the UI when we don't already have one stored.
        return not self.coordinator.client.has_pin

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        data = self.coordinator.data
        return data.panel.state if data else None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._run("Arm away", self.coordinator.client.arm, "away", code=code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._run("Arm home", self.coordinator.client.arm, "home", code=code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._run("Disarm", self.coordinator.client.disarm, code=code)

    async def _run(self, label: str, fn, *args, code: str | None) -> None:
        try:
            await fn(*args, code=code)
        except SectorApiError as err:
            raise HomeAssistantError(f"{label} failed: {err}") from err
        await self.coordinator.async_request_refresh()
