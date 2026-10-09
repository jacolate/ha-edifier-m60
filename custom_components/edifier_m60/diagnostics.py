"""Diagnostics for Edifier M60."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import M60ConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: M60ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device = entry.runtime_data
    info = bluetooth.async_last_service_info(hass, device.address, connectable=True)
    return {
        "address": device.address,
        "classic_mac": device.classic_mac,
        "mode": device.mode,
        "encrypted": device.encrypted,
        "volume_supported": device.volume_supported,
        "state": asdict(device.state),
        "last_advertisement": None
        if info is None
        else {
            "name": info.name,
            "rssi": info.rssi,
            "source": info.source,
            "manufacturer_data": {
                f"0x{k:04X}": v.hex(" ") for k, v in info.manufacturer_data.items()
            },
            "service_uuids": info.service_uuids,
        },
        "events": list(device.events),
        "frames": list(device.frames),
    }
