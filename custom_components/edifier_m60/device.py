"""BLE connection and state handling for an Edifier M60."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    CMD_DEVICE_EXCEPTION,
    CMD_INPUT_QUERY,
    CMD_INPUT_SET,
    CMD_VOLUME_QUERY,
    CMD_VOLUME_SET,
    DEFAULT_INPUT_TYPE,
    INPUT_MAPS,
    MODE_PERSISTENT,
    NOTIFY_CHAR_UUID,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    REQUEST_TIMEOUT,
    VOLUME_PROBE_TIMEOUT,
    WRITE_CHAR_UUID,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class M60State:
    """Last known state of the speaker."""

    input_type: int | None = None
    input_value: int | None = None
    volume: int | None = None
    connected: bool = False

    @property
    def input_name(self) -> str | None:
        """Return the input as a select option, or None if unknown."""
        if self.input_value is None:
            return None
        mapping = INPUT_MAPS.get(
            self.input_type or DEFAULT_INPUT_TYPE, INPUT_MAPS[DEFAULT_INPUT_TYPE]
        )
        return mapping.get(self.input_value)


def _describe(err: Exception) -> str:
    """Return a readable reason; str(TimeoutError()) is empty."""
    if isinstance(err, TimeoutError):
        return "no response from speaker"
    return str(err) or type(err).__name__


class M60Device:
    """Owns the BLE link to one speaker and exposes its state to entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        ble_device: BLEDevice,
        encrypted: bool,
        mode: str,
        poll_interval: int,
        idle_disconnect: int,
    ) -> None:
        """Initialize the device; call async_start() to connect."""
        self.hass = hass
        self.address = address
        self.name = name
        self.mode = mode
        self.state = M60State()
        self.classic_mac: str | None = None
        # None = not probed yet, True/False after the first 0x66 query
        self.volume_supported: bool | None = None

        self._ble_device = ble_device
        self._poll_interval = poll_interval
        self._idle_disconnect = idle_disconnect
        self._buffer = protocol.FrameBuffer(encrypted)
        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._idle_unsub: CALLBACK_TYPE | None = None
        self._loop_task: asyncio.Task | None = None
        self._disconnected = asyncio.Event()
        self._stopping = False
        self._present = True
        self._last_error: str | None = None

        # Kept for diagnostics
        self.events: deque[tuple[str, str]] = deque(maxlen=50)
        self.frames: deque[tuple[str, str]] = deque(maxlen=50)

    # ------------------------------------------------------------------
    # Lifecycle

    @property
    def encrypted(self) -> bool:
        """Return whether payloads are XOR encrypted."""
        return self._buffer.encrypted

    @property
    def available(self) -> bool:
        """Return whether entities should be available."""
        if self.mode == MODE_PERSISTENT:
            return self.state.connected
        return self._present and self._last_error is None

    async def async_start(self) -> None:
        """Start tracking adverts and connect or schedule polling."""
        self._unsubs.append(
            bluetooth.async_register_callback(
                self.hass,
                self._on_advertisement,
                bluetooth.BluetoothCallbackMatcher(
                    address=self.address, connectable=True
                ),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        self._unsubs.append(
            bluetooth.async_track_unavailable(
                self.hass, self._on_unavailable, self.address, connectable=True
            )
        )
        if self.mode == MODE_PERSISTENT:
            self._loop_task = self.hass.async_create_background_task(
                self._persistent_loop(), f"edifier_m60 {self.address}"
            )
        else:
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._on_poll_timer,
                    timedelta(seconds=self._poll_interval),
                )
            )
            self.hass.async_create_background_task(
                self._poll(), f"edifier_m60 initial poll {self.address}"
            )

    async def async_stop(self) -> None:
        """Stop all tasks and disconnect."""
        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_idle_timer()
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        await self._disconnect()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a callback for state changes."""
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _event(self, msg: str, *args: Any, level: int = logging.INFO) -> None:
        text = msg % args if args else msg
        self.events.append((dt_util.utcnow().isoformat(), text))
        _LOGGER.log(level, "%s: %s", self.name, text)

    # ------------------------------------------------------------------
    # Advertisement tracking (useful for the "does BLE work without A2DP" question)

    @callback
    def _on_advertisement(
        self,
        info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self._ble_device = info.device
        mfr = info.manufacturer_data.get(0x07E0)
        if mfr:
            encrypted = protocol.parse_manufacturer_data(mfr).encrypted
            if encrypted != self._buffer.encrypted:
                self._event("Encryption flag changed to %s", encrypted)
                self._buffer.encrypted = encrypted
        if not self._present:
            self._present = True
            self._event("Advertising again (rssi %s, via %s)", info.rssi, info.source)
            self._notify_listeners()

    @callback
    def _on_unavailable(self, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if self._present:
            self._present = False
            self._event("No longer advertising (last seen via %s)", info.source)
            self._notify_listeners()

    # ------------------------------------------------------------------
    # Connection handling

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return self._client
            ble_device = (
                bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                or self._ble_device
            )
            self._event("Connecting (present=%s)", self._present, level=logging.DEBUG)
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.name,
                    disconnected_callback=self._on_disconnect,
                    ble_device_callback=lambda: self._ble_device,
                )
            except (BleakError, TimeoutError) as err:
                self._set_error(
                    f"Connect failed: {type(err).__name__}: {_describe(err)}"
                )
                raise
            self._buffer.reset()
            try:
                await client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            except (BleakError, TimeoutError) as err:
                await client.disconnect()
                self._set_error(
                    f"Connect failed: {type(err).__name__}: {_describe(err)}"
                )
                raise
            self._client = client
            self._disconnected.clear()
            self._last_error = None
            self.state.connected = True
            self._event("Connected")
            self._notify_listeners()
            return client

    def _set_error(self, error: str) -> None:
        # Log at INFO only when the error changes, so retries don't spam the log
        level = logging.INFO if error != self._last_error else logging.DEBUG
        self._last_error = error
        self._event(error, level=level)

    @callback
    def _on_disconnect(self, client: Any) -> None:
        if client is not self._client:
            return
        self._client = None
        self.state.connected = False
        self._disconnected.set()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(BleakError("Disconnected"))
        self._pending.clear()
        if not self._stopping:
            self._event("Disconnected")
        self._notify_listeners()

    async def _disconnect(self) -> None:
        async with self._connect_lock:
            client = self._client
            if client is None:
                return
            self._client = None
            self.state.connected = False
            try:
                await client.disconnect()
            except BleakError as err:
                _LOGGER.debug("%s: error while disconnecting: %s", self.name, err)
            self._disconnected.set()
            self._event("Disconnected by us", level=logging.DEBUG)
        self._notify_listeners()

    async def _persistent_loop(self) -> None:
        delay = RECONNECT_MIN_DELAY
        while not self._stopping:
            try:
                await self._ensure_connected()
                await self.async_refresh()
                delay = RECONNECT_MIN_DELAY
                await self._disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                _LOGGER.debug("%s: connection loop error: %s", self.name, err)
            # async_stop() cancels this task, which interrupts the sleep
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    # On-demand mode -----------------------------------------------------

    @callback
    def _on_poll_timer(self, _now: datetime) -> None:
        self.hass.async_create_background_task(
            self._poll(), f"edifier_m60 poll {self.address}"
        )

    async def _poll(self) -> None:
        try:
            await self.async_refresh()
        except (BleakError, TimeoutError, HomeAssistantError) as err:
            _LOGGER.debug("%s: poll failed: %s", self.name, err)
            self._notify_listeners()

    def _cancel_idle_timer(self) -> None:
        if self._idle_unsub:
            self._idle_unsub()
            self._idle_unsub = None

    def _schedule_idle_disconnect(self) -> None:
        if self.mode == MODE_PERSISTENT or self._stopping:
            return
        self._cancel_idle_timer()

        @callback
        def _fire(_now: datetime) -> None:
            self._idle_unsub = None
            self.hass.async_create_background_task(
                self._disconnect(), f"edifier_m60 idle disconnect {self.address}"
            )

        self._idle_unsub = async_call_later(self.hass, self._idle_disconnect, _fire)

    # ------------------------------------------------------------------
    # Requests and notifications

    async def _request(
        self,
        cmd: int,
        payload: bytes | list[int] = b"",
        response_timeout: float | None = None,
    ) -> bytes:
        """Send a command and wait for the response frame with the same cmd."""
        async with self._request_lock:
            self._cancel_idle_timer()
            try:
                client = await self._ensure_connected()
                fut: asyncio.Future[bytes] = self.hass.loop.create_future()
                self._pending[cmd] = fut
                data = protocol.build(cmd, payload, self._buffer.encrypted)
                _LOGGER.debug("%s: TX %s", self.name, data.hex(" "))
                try:
                    await client.write_gatt_char(WRITE_CHAR_UUID, data, response=True)
                    async with asyncio.timeout(response_timeout or REQUEST_TIMEOUT):
                        return await fut
                finally:
                    if self._pending.get(cmd) is fut:
                        del self._pending[cmd]
            finally:
                self._schedule_idle_disconnect()

    @callback
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        _LOGGER.debug("%s: RX %s", self.name, bytes(data).hex(" "))
        for cmd, payload in self._buffer.feed(bytes(data)):
            self.frames.append((f"{cmd:02X}", payload.hex(" ")))
            self._handle_frame(cmd, payload)

    @callback
    def _handle_frame(self, cmd: int, payload: bytes) -> None:
        changed = False
        if cmd == CMD_INPUT_QUERY and len(payload) >= 2:
            new = (payload[0], payload[1])
            if new != (self.state.input_type, self.state.input_value):
                self.state.input_type, self.state.input_value = new
                changed = True
                _LOGGER.debug(
                    "%s: input type=0x%02X value=0x%02X (%s)",
                    self.name,
                    new[0],
                    new[1],
                    self.state.input_name,
                )
        elif cmd in (CMD_VOLUME_QUERY, CMD_VOLUME_SET) and payload:
            # Payload layout unverified: assume the first byte is the volume
            _LOGGER.debug(
                "%s: volume frame 0x%02X payload %s", self.name, cmd, payload.hex(" ")
            )
            if cmd == CMD_VOLUME_QUERY and payload[0] != self.state.volume:
                self.state.volume = payload[0]
                changed = True
        elif cmd == CMD_DEVICE_EXCEPTION:
            _LOGGER.warning(
                "%s: device exception frame: %s", self.name, payload.hex(" ")
            )
        elif cmd not in self._pending:
            _LOGGER.debug(
                "%s: ignoring frame 0x%02X: %s", self.name, cmd, payload.hex(" ")
            )

        fut = self._pending.get(cmd)
        if fut and not fut.done():
            fut.set_result(payload)
        if changed:
            self._notify_listeners()

    # ------------------------------------------------------------------
    # Public API

    async def async_refresh(self) -> None:
        """Query input source (and volume, if supported)."""
        await self._request(CMD_INPUT_QUERY)
        if self.volume_supported is False:
            return
        try:
            await self._request(CMD_VOLUME_QUERY, response_timeout=VOLUME_PROBE_TIMEOUT)
        except TimeoutError:
            if self.volume_supported is None:
                self.volume_supported = False
                self._event("No answer to volume query, volume entity disabled")
            return
        if self.volume_supported is None:
            self.volume_supported = True
            self._event("Volume query answered (volume=%s)", self.state.volume)
            self._notify_listeners()

    async def async_set_input(self, option: str) -> None:
        """Switch the input source."""
        input_type = self.state.input_type or DEFAULT_INPUT_TYPE
        mapping = INPUT_MAPS.get(input_type, INPUT_MAPS[DEFAULT_INPUT_TYPE])
        value = next((raw for raw, name in mapping.items() if name == option), None)
        if value is None:
            raise HomeAssistantError(f"Unknown input {option}")
        try:
            result = await self._request(CMD_INPUT_SET, [input_type, value])
        except (BleakError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Failed to set input on {self.name}: {_describe(err)}"
            ) from err
        if result[:1] != b"\x01":
            raise HomeAssistantError(
                f"{self.name} rejected input change: {result.hex(' ')}"
            )
        # The speaker pushes a 0x61 frame with the new state; set it optimistically too
        self.state.input_type, self.state.input_value = input_type, value
        self._notify_listeners()

    async def async_set_volume(self, volume: int) -> None:
        """Set the volume and read it back."""
        try:
            await self._request(CMD_VOLUME_SET, [volume])
        except TimeoutError:
            # Ack format unknown; fall through and read back the actual value
            _LOGGER.debug("%s: no ack for volume set", self.name)
        except BleakError as err:
            raise HomeAssistantError(
                f"Failed to set volume on {self.name}: {_describe(err)}"
            ) from err
        try:
            await self._request(CMD_VOLUME_QUERY, response_timeout=VOLUME_PROBE_TIMEOUT)
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: volume read-back failed: %s", self.name, err)
