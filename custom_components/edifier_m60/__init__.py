"""The Edifier M60 integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import protocol
from .const import (
    CONF_CONNECTION_MODE,
    CONF_IDLE_DISCONNECT,
    CONF_POLL_INTERVAL,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_IDLE_DISCONNECT,
    DEFAULT_POLL_INTERVAL,
    MANUFACTURER_ID,
)
from .device import M60Device

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT]

type M60ConfigEntry = ConfigEntry[M60Device]


async def async_setup_entry(hass: HomeAssistant, entry: M60ConfigEntry) -> bool:
    """Set up an Edifier M60 from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Edifier M60 {address} is not advertising (is a connectable adapter or proxy in range?)"
        )

    service_info = bluetooth.async_last_service_info(hass, address, connectable=True)
    mfr = service_info.manufacturer_data.get(MANUFACTURER_ID) if service_info else None
    info = protocol.parse_manufacturer_data(mfr)

    device = M60Device(
        hass,
        address,
        entry.title,
        ble_device,
        info.encrypted,
        entry.options.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
        int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        int(entry.options.get(CONF_IDLE_DISCONNECT, DEFAULT_IDLE_DISCONNECT)),
    )
    device.classic_mac = info.classic_mac
    entry.runtime_data = device

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await device.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: M60ConfigEntry) -> bool:
    """Unload a config entry and close the BLE link."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: M60ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
