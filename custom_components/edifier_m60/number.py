"""Volume number for Edifier M60 (created once the speaker answers a volume query)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import M60ConfigEntry
from .const import VOLUME_MAX, VOLUME_MIN
from .entity import M60Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: M60ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the volume entity once the speaker supports it."""
    device = entry.runtime_data
    added = False

    @callback
    def _maybe_add() -> None:
        nonlocal added
        if not added and device.volume_supported:
            added = True
            async_add_entities([M60Volume(device, "volume")])

    entry.async_on_unload(device.async_add_listener(_maybe_add))
    _maybe_add()


class M60Volume(M60Entity, NumberEntity):
    """Speaker volume."""

    _attr_translation_key = "volume"
    _attr_native_min_value = VOLUME_MIN
    _attr_native_max_value = VOLUME_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        """Return the current volume."""
        return self._device.state.volume

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume."""
        await self._device.async_set_volume(int(value))
