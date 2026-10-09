"""Tests for the select, number and binary_sensor entities."""

from __future__ import annotations

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeSpeaker, setup_entry, wait_until

SELECT = "select.edifier_m60_88_d2_input_source"
NUMBER = "number.edifier_m60_88_d2_volume"
CONNECTED = "binary_sensor.edifier_m60_88_d2_connected"


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT, ATTR_OPTION: option},
        blocking=True,
    )


async def test_entities_created(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    entity_registry: er.EntityRegistry,
) -> None:
    """All entities are created with stable unique ids."""
    await setup_entry(hass, mock_entry)
    entries = er.async_entries_for_config_entry(entity_registry, mock_entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entries} == {
        SELECT: "AA:BB:CC:DD:EE:FF_input_source",
        NUMBER: "AA:BB:CC:DD:EE:FF_volume",
        CONNECTED: "AA:BB:CC:DD:EE:FF_connected",
    }
    state = hass.states.get(SELECT)
    assert state.state == "Bluetooth"
    assert state.attributes["options"] == ["Bluetooth", "USB-C", "AUX"]
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get(NUMBER).state == "20"


@pytest.mark.parametrize(
    ("option", "packet"),
    [
        ("Bluetooth", "aa ec 62 00 02 0f 01 0a"),
        ("USB-C", "aa ec 62 00 02 0f 02 0b"),
        ("AUX", "aa ec 62 00 02 0f 03 0c"),
    ],
)
async def test_select_option_sends_packet(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    option: str,
    packet: str,
) -> None:
    """Selecting an input writes the exact packet verified on the speaker."""
    await setup_entry(hass, mock_entry)
    await _select(hass, option)
    assert speaker.written[-1] == bytes.fromhex(packet)
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == option


async def test_select_uses_reported_input_type(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Type 0x02 speakers swap the USB and AUX values."""
    speaker.input_type = 0x02
    await setup_entry(hass, mock_entry)
    await _select(hass, "AUX")
    assert speaker.written[-1] == bytes.fromhex("aa ec 62 00 02 02 02 fe")
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == "AUX"


async def test_input_changed_on_speaker(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Unsolicited 0x61 pushes update the select."""
    await setup_entry(hass, mock_entry)
    speaker.push(0x61, [0x0F, 0x03])
    await wait_until(hass, lambda: hass.states.get(SELECT).state == "AUX")


async def test_unknown_input_value(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """An input value we don't know shows as unknown instead of crashing."""
    await setup_entry(hass, mock_entry)
    speaker.push(0x61, [0x0F, 0x09])
    await wait_until(hass, lambda: hass.states.get(SELECT).state == "unknown")


async def test_select_rejected(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """A non-OK ack raises and leaves the state unchanged."""
    await setup_entry(hass, mock_entry)
    speaker.answer_input_set = 0x00
    with pytest.raises(HomeAssistantError, match="rejected"):
        await _select(hass, "AUX")
    assert hass.states.get(SELECT).state == "Bluetooth"


async def test_select_timeout(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """No ack raises a HomeAssistantError."""
    await setup_entry(hass, mock_entry)
    speaker.silent.add(0x62)
    with pytest.raises(
        HomeAssistantError, match=r"Failed to set input on .*: no response from speaker"
    ):
        await _select(hass, "AUX")


async def test_volume_set(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Setting the volume writes 0x67 and reads the value back."""
    await setup_entry(hass, mock_entry)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: NUMBER, ATTR_VALUE: 12},
        blocking=True,
    )
    assert bytes.fromhex("aa ec 67 00 01 0c 0a") in speaker.written
    await hass.async_block_till_done()
    assert hass.states.get(NUMBER).state == "12"


async def test_volume_set_without_ack(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """A missing ack for 0x67 is tolerated; the read-back decides the state."""
    await setup_entry(hass, mock_entry)
    speaker.silent.add(0x67)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: NUMBER, ATTR_VALUE: 5},
        blocking=True,
    )
    assert hass.states.get(NUMBER).state == "20"


async def test_no_volume_entity_when_unsupported(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Speakers that ignore 0x66 get no volume entity."""
    speaker.volume = None
    await setup_entry(hass, mock_entry)
    assert hass.states.get(NUMBER) is None
    assert mock_entry.runtime_data.volume_supported is False


async def test_disconnect_and_reconnect(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Persistent mode marks entities unavailable and reconnects."""
    await setup_entry(hass, mock_entry)
    assert speaker.establish.call_count == 1

    speaker.drop()
    await hass.async_block_till_done()
    assert hass.states.get(CONNECTED).state == STATE_OFF
    assert hass.states.get(SELECT).state == STATE_UNAVAILABLE

    await wait_until(hass, lambda: hass.states.get(CONNECTED).state == STATE_ON)
    assert speaker.establish.call_count == 2
    assert hass.states.get(SELECT).state == "Bluetooth"
