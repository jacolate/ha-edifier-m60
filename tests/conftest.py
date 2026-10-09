"""Shared fixtures for Edifier M60 tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import time
from typing import Any
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BluetoothServiceInfoBleak, get_manager
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.edifier_m60 import protocol
from custom_components.edifier_m60.const import DOMAIN, SEARCH_SERVICE_UUID

ADDRESS = "AA:BB:CC:DD:EE:FF"
MFR_DATA = bytes.fromhex("c8 24 78 80 88 d2 02 00")
TITLE = "Edifier M60 (88:D2)"


def make_service_info(
    address: str = ADDRESS,
    manufacturer_data: dict[int, bytes] | None = None,
    service_uuids: list[str] | None = None,
    name: str = "EDIFIER BLE",
) -> BluetoothServiceInfoBleak:
    """Build an advertisement like the one the M60 sends."""
    if manufacturer_data is None:
        manufacturer_data = {0x07E0: MFR_DATA}
    if service_uuids is None:
        service_uuids = [SEARCH_SERVICE_UUID]
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=service_uuids,
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=service_uuids,
        source="local",
        device=BLEDevice(address, name, None),
        advertisement=adv,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


M60_SERVICE_INFO = make_service_info()
OTHER_SERVICE_INFO = make_service_info(
    address="11:22:33:44:55:66",
    manufacturer_data={0x1234: b"\x01\x02"},
    service_uuids=[],
    name="Other",
)


def inject(hass: HomeAssistant, info: BluetoothServiceInfoBleak) -> None:
    """Feed an advertisement into HA's bluetooth manager."""
    get_manager().scanner_adv_received(info)


class FakeSpeaker:
    """Stands in for BleakClient and emulates the speaker's protocol."""

    def __init__(self) -> None:
        """Start on Bluetooth input with volume 20."""
        self.is_connected = True
        self.input = 0x01
        self.input_type = 0x0F
        self.volume: int | None = 20
        self.answer_input_set = 0x01
        self.silent: set[int] = set()
        self.written: list[bytes] = []
        self.encrypted = False
        self.fail_notify = False
        self._notify_cb: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None

    async def start_notify(
        self, _uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        """Remember the notify callback."""
        if self.fail_notify:
            from bleak.exc import BleakError

            raise BleakError("notify failed")
        self._notify_cb = callback

    async def disconnect(self) -> None:
        """Disconnect and fire the callback like bleak does."""
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)

    def drop(self) -> None:
        """Simulate the speaker dropping the link."""
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)

    def push(self, cmd: int, payload: list[int]) -> None:
        """Send a frame to the integration, split over two notifications."""
        body = bytes(payload)
        if self.encrypted:
            body = bytes(b ^ 0xA5 for b in body)
        frame = bytes([0xBB, 0xEC, cmd, 0, len(body)]) + body
        frame += bytes([sum(frame) & 0xFF])
        assert self._notify_cb is not None
        loop = asyncio.get_running_loop()
        loop.call_soon(self._notify_cb, None, bytearray(frame[:3]))
        loop.call_soon(self._notify_cb, None, bytearray(frame[3:]))

    async def write_gatt_char(self, _uuid: str, data: bytes, response: bool) -> None:
        """Answer commands the way the M60 does."""
        assert response
        self.written.append(bytes(data))
        parsed = protocol.parse(bytes(data), self.encrypted)
        assert parsed is not None, f"integration sent invalid frame {data.hex(' ')}"
        cmd, payload = parsed
        if cmd in self.silent:
            return
        if cmd == 0x61:
            self.push(0x61, [self.input_type, self.input])
            self.push(0x84, [0x03, 0x01, 0x00])
        elif cmd == 0x62:
            if self.answer_input_set == 0x01:
                self.input = payload[1]
            self.push(0x62, [self.answer_input_set])
            if self.answer_input_set == 0x01:
                self.push(0x61, [self.input_type, self.input])
        elif cmd == 0x66 and self.volume is not None:
            self.push(0x66, [self.volume])
        elif cmd == 0x67 and self.volume is not None:
            self.volume = payload[0]
            self.push(0x67, [0x01])


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


@pytest.fixture(autouse=True)
def auto_enable_bluetooth(
    enable_custom_integrations: None, enable_bluetooth: None
) -> None:
    """Set up a mocked bluetooth stack in every test.

    Custom integrations must be enabled first, otherwise bluetooth builds its
    discovery matchers without ours.
    """


@pytest.fixture
def speaker() -> Generator[FakeSpeaker]:
    """Patch the BLE connection to return a FakeSpeaker."""
    fake = FakeSpeaker()

    async def _establish(
        _cls: Any, _device: Any, _name: str, **kwargs: Any
    ) -> FakeSpeaker:
        fake.is_connected = True
        fake.disconnected_callback = kwargs["disconnected_callback"]
        return fake

    with patch(
        "custom_components.edifier_m60.device.establish_connection",
        side_effect=_establish,
    ) as mock:
        fake.establish = mock  # type: ignore[attr-defined]
        yield fake


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for the test speaker."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title=TITLE, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def fast_timeouts() -> Generator[None]:
    """Shrink timeouts and backoff so tests run quickly."""
    base = "custom_components.edifier_m60.device"
    with (
        patch(f"{base}.REQUEST_TIMEOUT", 0.2),
        patch(f"{base}.VOLUME_PROBE_TIMEOUT", 0.1),
        patch(f"{base}.RECONNECT_MIN_DELAY", 0.01),
        patch(f"{base}.RECONNECT_MAX_DELAY", 0.02),
    ):
        yield


async def wait_until(
    hass: HomeAssistant, predicate: Callable[[], bool], limit: float = 2
) -> None:
    """Let the event loop run until predicate() is true."""
    async with asyncio.timeout(limit):
        while not predicate():
            await asyncio.sleep(0.005)
    await hass.async_block_till_done()


async def setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    info: BluetoothServiceInfoBleak = M60_SERVICE_INFO,
    wait: bool = True,
) -> None:
    """Advertise the speaker, set up the entry and wait for the first refresh."""
    inject(hass, info)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if wait:
        device = entry.runtime_data
        await wait_until(
            hass,
            lambda: (
                device.volume_supported is not None
                and device.state.input_value is not None
            ),
        )
