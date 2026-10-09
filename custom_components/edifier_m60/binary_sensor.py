"""BLE connection binary sensor for Edifier M60."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import M60ConfigEntry
from .entity import M60Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: M60ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the connection binary sensor."""
    async_add_entities([M60ConnectedSensor(entry.runtime_data, "connected")])


class M60ConnectedSensor(M60Entity, BinarySensorEntity):
    """Whether Home Assistant currently holds the BLE link."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Always available, so it can show "disconnected"
        """Stay available so the sensor can show disconnected."""
        return True

    @property
    def is_on(self) -> bool:
        """Return true while connected."""
        return self._device.state.connected
