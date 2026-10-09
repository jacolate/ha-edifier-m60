"""Input source select for Edifier M60."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import M60ConfigEntry
from .const import INPUT_OPTIONS
from .entity import M60Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: M60ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the input source select."""
    async_add_entities([M60InputSelect(entry.runtime_data, "input_source")])


class M60InputSelect(M60Entity, SelectEntity):
    """Input source of the speaker."""

    _attr_translation_key = "input_source"
    _attr_options = INPUT_OPTIONS

    @property
    def current_option(self) -> str | None:
        """Return the current input."""
        return self._device.state.input_name

    async def async_select_option(self, option: str) -> None:
        """Switch the input."""
        await self._device.async_set_input(option)
