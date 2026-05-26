"""Temperature and humidity sensors for Sector Alarm."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
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
    known_temps: set[str] = set()
    known_humids: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        new: list[SensorEntity] = []
        for t in data.temperatures:
            if t.serial and t.serial not in known_temps:
                new.append(SectorTemperatureSensor(coordinator, t.serial))
                known_temps.add(t.serial)
        for h in data.humidities:
            if h.serial and h.serial not in known_humids:
                new.append(SectorHumiditySensor(coordinator, h.serial))
                known_humids.add(h.serial)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class SectorTemperatureSensor(SectorBaseEntity, SensorEntity):
    """A single Sector temperature reading."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: SectorDataUpdateCoordinator, serial: str
    ) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{coordinator.client.panel_id}_temp_{serial}"

    def _lookup(self):
        data = self.coordinator.data
        if not data:
            return None
        for t in data.temperatures:
            if t.serial == self.serial:
                return t
        return None

    @property
    def available(self) -> bool:
        return super().available and self._lookup() is not None

    @property
    def name(self) -> str | None:
        sensor = self._lookup()
        return sensor.name if sensor else None

    @property
    def native_value(self) -> float | None:
        sensor = self._lookup()
        return sensor.temperature if sensor else None


class SectorHumiditySensor(SectorBaseEntity, SensorEntity):
    """A single Sector humidity reading (typically from a smoke detector)."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: SectorDataUpdateCoordinator, serial: str
    ) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{coordinator.client.panel_id}_humidity_{serial}"

    def _lookup(self):
        data = self.coordinator.data
        if not data:
            return None
        for h in data.humidities:
            if h.serial == self.serial:
                return h
        return None

    @property
    def available(self) -> bool:
        return super().available and self._lookup() is not None

    @property
    def name(self) -> str | None:
        # Suffix with " humidity" so it doesn't collide with the temperature
        # entity for the same physical device.
        sensor = self._lookup()
        return f"{sensor.name} humidity" if sensor else None

    @property
    def native_value(self) -> float | None:
        sensor = self._lookup()
        return sensor.humidity if sensor else None
