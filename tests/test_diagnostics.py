"""Tests for diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import ADDRESS, FakeSpeaker, setup_entry


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
) -> None:
    """Diagnostics include state, the last advert and recent frames."""
    await setup_entry(hass, mock_entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)

    assert diag["address"] == ADDRESS
    assert diag["classic_mac"] == "C8:24:78:80:88:D2"
    assert diag["mode"] == "persistent"
    assert diag["volume_supported"] is True
    assert diag["state"] == {
        "input_type": 15,
        "input_value": 1,
        "volume": 20,
        "connected": True,
    }
    assert diag["last_advertisement"]["manufacturer_data"] == {
        "0x07E0": "c8 24 78 80 88 d2 02 00"
    }
    assert ["61", "0f 01"] in [list(f) for f in diag["frames"]]
    assert any("Connected" in text for _, text in diag["events"])
