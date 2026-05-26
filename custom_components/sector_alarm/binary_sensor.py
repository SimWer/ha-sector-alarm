"""Binary sensors for Sector Alarm door/window contacts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SectorDataUpdateCoordinator
from .entity import SectorBaseEntity
from .models import SectorConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SectorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        new = [
            SectorContactSensor(coordinator, contact.serial)
            for contact in data.contacts
            if contact.serial and contact.serial not in known
        ]
        for entity in new:
            known.add(entity.serial)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class SectorContactSensor(SectorBaseEntity, BinarySensorEntity):
    """Door or window contact — `on` means the door/window is open."""

    def __init__(
        self, coordinator: SectorDataUpdateCoordinator, serial: str
    ) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{coordinator.client.panel_id}_contact_{serial}"

    def _lookup(self):
        data = self.coordinator.data
        if not data:
            return None
        for c in data.contacts:
            if c.serial == self.serial:
                return c
        return None

    @property
    def available(self) -> bool:
        return super().available and self._lookup() is not None

    @property
    def name(self) -> str | None:
        contact = self._lookup()
        return contact.name if contact else None

    @property
    def is_on(self) -> bool | None:
        contact = self._lookup()
        if contact is None:
            return None
        return not contact.closed  # binary sensor "on" = open

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        contact = self._lookup()
        if contact is None:
            return None
        return (
            BinarySensorDeviceClass.WINDOW
            if contact.type.lower() == "window"
            else BinarySensorDeviceClass.DOOR
        )
