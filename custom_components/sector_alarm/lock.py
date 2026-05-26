"""Lock platform for Sector Alarm smart locks."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
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
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        new = [
            SectorLock(coordinator, lock.serial)
            for lock in data.locks
            if lock.serial and lock.serial not in known
        ]
        for entity in new:
            known.add(entity.serial)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class SectorLock(SectorBaseEntity, LockEntity):
    """A Sector smart lock."""

    def __init__(
        self, coordinator: SectorDataUpdateCoordinator, serial: str
    ) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{coordinator.client.panel_id}_lock_{serial}"

    def _lookup(self):
        data = self.coordinator.data
        if not data:
            return None
        for lock in data.locks:
            if lock.serial == self.serial:
                return lock
        return None

    @property
    def available(self) -> bool:
        return super().available and self._lookup() is not None

    @property
    def name(self) -> str | None:
        lock = self._lookup()
        return lock.name if lock else None

    @property
    def is_locked(self) -> bool | None:
        lock = self._lookup()
        return lock.locked if lock else None

    async def async_lock(self, **kwargs) -> None:
        try:
            await self.coordinator.client.lock_door(self.serial)
        except SectorApiError as err:
            raise HomeAssistantError(f"Lock failed: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, code: str | None = None, **kwargs) -> None:
        try:
            await self.coordinator.client.unlock_door(self.serial, code=code)
        except SectorApiError as err:
            raise HomeAssistantError(f"Unlock failed: {err}") from err
        await self.coordinator.async_request_refresh()
