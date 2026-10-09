"""Base entity for Edifier M60."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .device import M60Device


class M60Entity(Entity):
    """Base class for Edifier M60 entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: M60Device, key: str) -> None:
        """Initialize the entity."""
        self._device = device
        self._attr_unique_id = f"{device.address}_{key}"
        connections = {(CONNECTION_BLUETOOTH, device.address)}
        if device.classic_mac:
            connections.add((CONNECTION_BLUETOOTH, device.classic_mac))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            connections=connections,
            name=device.name,
            manufacturer="Edifier",
            model="M60",
        )

    @property
    def available(self) -> bool:
        """Return whether the speaker is reachable."""
        return self._device.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes."""
        self.async_on_remove(self._device.async_add_listener(self.async_write_ha_state))
