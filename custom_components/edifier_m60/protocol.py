"""Edifier BLE protocol (version 2): packet building and parsing.

Pure Python, no Home Assistant imports, so it can be unit tested standalone.

Command  (app -> speaker): AA EC <cmd> <len_hi> <len_lo> <payload...> <checksum>
Response (speaker -> app): BB EC <cmd> <len_hi> <len_lo> <payload...> <checksum>
checksum = sum(all preceding bytes) & 0xFF
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

_LOGGER = logging.getLogger(__name__)

HEADER_APP = 0xAA
HEADER_DEVICE = 0xBB
APP_CODE = 0xEC
XOR_KEY = 0xA5
ENCRYPTION_FLAG = 0x10

# Frame overhead: 2 header bytes + cmd + 2 length bytes + checksum
_OVERHEAD = 6
# Sanity cap so a corrupted length byte can't make us wait forever
_MAX_PAYLOAD = 512


def _xor(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY for b in data)


def build(cmd: int, payload: bytes | list[int] = b"", encrypted: bool = False) -> bytes:
    """Build a command frame to send to the speaker."""
    payload = bytes(payload)
    if encrypted:
        payload = _xor(payload)
    data = bytes(
        [HEADER_APP, APP_CODE, cmd & 0xFF, len(payload) >> 8, len(payload) & 0xFF]
    )
    data += payload
    return data + bytes([sum(data) & 0xFF])


def parse(frame: bytes, encrypted: bool = False) -> tuple[int, bytes] | None:
    """Parse one complete frame. Returns (cmd, payload) or None if invalid."""
    if (
        len(frame) < _OVERHEAD
        or frame[0] not in (HEADER_APP, HEADER_DEVICE)
        or frame[1] != APP_CODE
    ):
        return None
    length = (frame[3] << 8) | frame[4]
    if len(frame) != length + _OVERHEAD or (sum(frame[:-1]) & 0xFF) != frame[-1]:
        return None
    payload = bytes(frame[5 : 5 + length])
    if encrypted:
        payload = _xor(payload)
    return frame[2], payload


class FrameBuffer:
    """Reassemble frames from a stream of BLE notifications.

    Handles frames split across notifications, several frames in one
    notification and garbage bytes between frames.
    """

    def __init__(self, encrypted: bool = False) -> None:
        """Initialize an empty buffer."""
        self.encrypted = encrypted
        self._buf = bytearray()

    def reset(self) -> None:
        """Drop any partial frame, e.g. after reconnecting."""
        self._buf.clear()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """Add notification bytes and return all complete frames."""
        self._buf += data
        frames: list[tuple[int, bytes]] = []
        while True:
            start = self._find_header()
            if start < 0:
                # Keep a trailing header byte, it might be the start of the next frame
                keep = (
                    1
                    if self._buf and self._buf[-1] in (HEADER_APP, HEADER_DEVICE)
                    else 0
                )
                if len(self._buf) > keep:
                    _LOGGER.debug(
                        "Dropping junk bytes: %s",
                        self._buf[: len(self._buf) - keep].hex(" "),
                    )
                del self._buf[: len(self._buf) - keep]
                break
            if start > 0:
                _LOGGER.debug("Dropping junk bytes: %s", self._buf[:start].hex(" "))
                del self._buf[:start]
            if len(self._buf) < 5:
                break
            length = (self._buf[3] << 8) | self._buf[4]
            if length > _MAX_PAYLOAD:
                _LOGGER.debug("Implausible length %d, resyncing", length)
                del self._buf[:1]
                continue
            total = length + _OVERHEAD
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            parsed = parse(frame, self.encrypted)
            if parsed is None:
                _LOGGER.debug("Bad checksum, resyncing: %s", frame.hex(" "))
                del self._buf[:1]
                continue
            del self._buf[:total]
            frames.append(parsed)
        return frames

    def _find_header(self) -> int:
        buf = self._buf
        for i in range(len(buf) - 1):
            if buf[i] in (HEADER_APP, HEADER_DEVICE) and buf[i + 1] == APP_CODE:
                return i
        return -1


@dataclass(frozen=True)
class ManufacturerInfo:
    """Fields parsed from the Edifier manufacturer data."""

    classic_mac: str | None
    protocol_version: int | None
    encrypted: bool


def parse_manufacturer_data(data: bytes | None) -> ManufacturerInfo:
    """Parse Edifier manufacturer data (company id 0x07E0).

    bytes 0-5: classic BT MAC, second to last: protocol version,
    last: encryption flag (0x10 = XOR payloads with 0xA5).
    """
    if not data or len(data) < 2:
        return ManufacturerInfo(None, None, False)
    mac = ":".join(f"{b:02X}" for b in data[:6]) if len(data) >= 8 else None
    return ManufacturerInfo(mac, data[-2], data[-1] == ENCRYPTION_FLAG)
