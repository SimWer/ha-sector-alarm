"""Shared base entity for Sector Alarm."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SectorDataUpdateCoordinator


class SectorBaseEntity(CoordinatorEntity[SectorDataUpdateCoordinator]):
    """Common base: groups all entities under the same Sector panel device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SectorDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        panel_id = coordinator.client.panel_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, panel_id)},
            manufacturer=MANUFACTURER,
            name=coordinator.data.panel.display_name if coordinator.data else f"Sector {panel_id}",
            model="Alarm Panel",
        )
