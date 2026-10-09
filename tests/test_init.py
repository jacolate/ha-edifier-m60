"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.edifier_m60.const import DOMAIN

from .conftest import ADDRESS, FakeSpeaker, setup_entry


async def test_setup_and_unload(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """The entry loads, connects, and disconnects cleanly on unload."""
    await setup_entry(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.LOADED
    assert speaker.is_connected

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.NOT_LOADED
    assert not speaker.is_connected


async def test_setup_retry_when_not_advertising(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Without an advertisement the entry is retried later."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_info(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The device carries both the BLE address and the classic MAC."""
    await setup_entry(hass, mock_entry)
    device = device_registry.async_get_device(identifiers={(DOMAIN, ADDRESS)})
    assert device is not None
    assert device.manufacturer == "Edifier"
    assert device.model == "M60"
    assert device.connections == {
        (dr.CONNECTION_BLUETOOTH, ADDRESS),
        (dr.CONNECTION_BLUETOOTH, "C8:24:78:80:88:D2"),
    }
