"""Tests for connection handling in M60Device."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from bleak.exc import BleakError
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.edifier_m60.const import (
    CONF_CONNECTION_MODE,
    CONF_IDLE_DISCONNECT,
    CONF_POLL_INTERVAL,
    MODE_ON_DEMAND,
)

from .conftest import (
    M60_SERVICE_INFO,
    FakeSpeaker,
    inject,
    make_service_info,
    setup_entry,
    wait_until,
)

SELECT = "select.edifier_m60_88_d2_input_source"
CONNECTED = "binary_sensor.edifier_m60_88_d2_connected"


@pytest.fixture
def on_demand_entry(
    mock_entry: MockConfigEntry, hass: HomeAssistant
) -> MockConfigEntry:
    """The test entry configured for on-demand connections."""
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            CONF_CONNECTION_MODE: MODE_ON_DEMAND,
            CONF_POLL_INTERVAL: 60,
            CONF_IDLE_DISCONNECT: 5,
        },
    )
    return mock_entry


async def test_connect_failure_is_retried(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed connect is logged once and retried until it works."""
    real = speaker.establish.side_effect
    attempts = 0

    async def _flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise BleakError("out of slots")
        return await real(*args, **kwargs)

    speaker.establish.side_effect = _flaky
    with caplog.at_level(logging.INFO):
        await setup_entry(hass, mock_entry)

    assert attempts == 3
    assert hass.states.get(CONNECTED).state == "on"
    # The same error twice is logged at INFO only once
    assert caplog.text.count("Connect failed: BleakError: out of slots") == 1


async def test_notify_failure_disconnects(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """If subscribing fails, the half-open link is closed."""
    speaker.fail_notify = True
    await setup_entry(hass, mock_entry, wait=False)
    await wait_until(hass, lambda: speaker.establish.call_count >= 2)
    assert not mock_entry.runtime_data.state.connected
    speaker.fail_notify = False
    await wait_until(hass, lambda: mock_entry.runtime_data.state.connected)


async def test_on_demand_idle_disconnect_and_poll(
    hass: HomeAssistant, on_demand_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """On-demand mode disconnects when idle and reconnects to poll."""
    await setup_entry(hass, on_demand_entry)
    device = on_demand_entry.runtime_data
    assert device.state.connected

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_until(hass, lambda: not device.state.connected)
    assert hass.states.get(CONNECTED).state == STATE_OFF
    # Still controllable, so the select stays available
    assert hass.states.get(SELECT).state == "Bluetooth"

    speaker.input = 0x03
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_until(hass, lambda: hass.states.get(SELECT).state == "AUX")
    assert speaker.establish.call_count == 2


async def test_on_demand_command_reconnects(
    hass: HomeAssistant, on_demand_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """A command while disconnected opens a new connection."""
    await setup_entry(hass, on_demand_entry)
    device = on_demand_entry.runtime_data
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_until(hass, lambda: not device.state.connected)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT, ATTR_OPTION: "USB-C"},
        blocking=True,
    )
    assert speaker.establish.call_count == 2
    assert hass.states.get(SELECT).state == "USB-C"


async def test_on_demand_unavailable_when_not_advertising(
    hass: HomeAssistant,
    on_demand_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Losing the advertisement makes entities unavailable (and is logged)."""
    await setup_entry(hass, on_demand_entry)
    device = on_demand_entry.runtime_data

    with caplog.at_level(logging.INFO):
        device._on_unavailable(M60_SERVICE_INFO)
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == STATE_UNAVAILABLE
        assert "No longer advertising" in caplog.text

        inject(hass, make_service_info(name="EDIFIER BLE 2"))
        await hass.async_block_till_done()
        assert hass.states.get(SELECT).state == "Bluetooth"
        assert "Advertising again" in caplog.text


async def test_encrypted_speaker(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """With the encryption flag set, payloads are XORed both ways."""
    speaker.encrypted = True
    info = make_service_info(
        manufacturer_data={0x07E0: bytes.fromhex("c8 24 78 80 88 d2 02 10")}
    )
    await setup_entry(hass, mock_entry, info=info)
    assert mock_entry.runtime_data.encrypted
    assert hass.states.get(SELECT).state == "Bluetooth"

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT, ATTR_OPTION: "AUX"},
        blocking=True,
    )
    assert speaker.written[-1][5:7] == bytes([0x0F ^ 0xA5, 0x03 ^ 0xA5])


async def test_encryption_flag_follows_advertisement(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """A change of the flag in later adverts is picked up."""
    await setup_entry(hass, mock_entry)
    assert not mock_entry.runtime_data.encrypted
    inject(
        hass,
        make_service_info(
            manufacturer_data={0x07E0: bytes.fromhex("c8 24 78 80 88 d2 02 10")}
        ),
    )
    await hass.async_block_till_done()
    assert mock_entry.runtime_data.encrypted


async def test_device_exception_frame_logged(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    speaker: FakeSpeaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """0x16 error frames are surfaced as warnings."""
    await setup_entry(hass, mock_entry)
    speaker.push(0x16, [0x01, 0x02])
    await wait_until(hass, lambda: "device exception frame: 01 02" in caplog.text)


async def test_disconnect_fails_pending_request(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """A request waiting for an answer fails fast when the link drops."""
    await setup_entry(hass, mock_entry)
    speaker.silent.add(0x62)
    device = mock_entry.runtime_data

    # Plain asyncio task: hass.async_block_till_done() would wait for it to time out
    task = asyncio.create_task(device.async_set_input("AUX"))
    while not device._pending:
        await asyncio.sleep(0.005)
    speaker.drop()
    with pytest.raises(
        HomeAssistantError, match=r"Failed to set input on .*: Disconnected"
    ):
        await task
