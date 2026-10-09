"""Tests for the frame protocol."""

from custom_components.edifier_m60 import protocol


def h(s: str) -> bytes:
    """Parse a hex string."""
    return bytes.fromhex(s)


def test_build_known_packets() -> None:
    """Packets match the bytes verified on the speaker."""
    assert protocol.build(0x61) == h("AA EC 61 00 00 F7")
    assert protocol.build(0x62, [0x0F, 0x01]) == h("AA EC 62 00 02 0F 01 0A")
    assert protocol.build(0x62, [0x0F, 0x02]) == h("AA EC 62 00 02 0F 02 0B")
    assert protocol.build(0x62, [0x0F, 0x03]) == h("AA EC 62 00 02 0F 03 0C")


def test_parse_known_responses() -> None:
    """Responses captured from the speaker parse correctly."""
    assert protocol.parse(h("BB EC 61 00 02 0F 01 1A")) == (0x61, h("0F 01"))
    assert protocol.parse(h("BB EC 61 00 02 0F 03 1C")) == (0x61, h("0F 03"))
    assert protocol.parse(h("BB EC 62 00 01 01 0B")) == (0x62, h("01"))
    assert protocol.parse(h("BB EC 84 00 03 03 01 00 32")) == (0x84, h("03 01 00"))
    assert protocol.parse(h("BB EC 50 00 05 00 00 00 00 00 FC")) == (0x50, bytes(5))


def test_parse_rejects_bad_frames() -> None:
    """Wrong checksum, length or header is rejected."""
    assert protocol.parse(h("BB EC 61 00 02 0F 01 1B")) is None  # checksum
    assert protocol.parse(h("BB EC 61 00 03 0F 01 1A")) is None  # length
    assert protocol.parse(h("CC EC 61 00 00 00")) is None  # header
    assert protocol.parse(h("BB EC")) is None


def test_buffer_multiple_frames_in_one_notification() -> None:
    """Several frames in one notification are all returned."""
    buf = protocol.FrameBuffer()
    data = h(
        "BB EC 62 00 01 01 0BBB EC 61 00 02 0F 03 1CBB EC 50 00 05 00 00 00 00 00 FC"
    )
    assert buf.feed(data) == [(0x62, h("01")), (0x61, h("0F 03")), (0x50, bytes(5))]


def test_buffer_split_frame() -> None:
    """A frame delivered byte by byte is reassembled."""
    buf = protocol.FrameBuffer()
    frame = h("BB EC 61 00 02 0F 03 1C")
    for i in range(len(frame) - 1):
        assert buf.feed(frame[i : i + 1]) == []
    assert buf.feed(frame[-1:]) == [(0x61, h("0F 03"))]


def test_buffer_split_after_first_header_byte() -> None:
    """A header split across notifications is kept."""
    buf = protocol.FrameBuffer()
    assert buf.feed(h("00 11 BB")) == []
    assert buf.feed(h("EC 61 00 02 0F 01 1A")) == [(0x61, h("0F 01"))]


def test_buffer_resyncs_after_junk_and_bad_checksum() -> None:
    """Junk and corrupt frames are skipped."""
    buf = protocol.FrameBuffer()
    data = h("01 02 BB EC 61 00 02 0F 01 FFBB EC 61 00 02 0F 03 1C")
    assert buf.feed(data) == [(0x61, h("0F 03"))]


def test_xor_round_trip() -> None:
    """XOR encryption applies to payload only and round-trips."""
    pkt = protocol.build(0x62, [0x0F, 0x03], encrypted=True)
    assert pkt[5:7] == bytes([0x0F ^ 0xA5, 0x03 ^ 0xA5])
    assert pkt[-1] == sum(pkt[:-1]) & 0xFF
    assert protocol.parse(pkt, encrypted=True) == (0x62, h("0F 03"))
    buf = protocol.FrameBuffer(encrypted=True)
    assert buf.feed(pkt) == [(0x62, h("0F 03"))]


def test_manufacturer_data() -> None:
    """Manufacturer data yields MAC, version and encryption flag."""
    info = protocol.parse_manufacturer_data(h("c8 24 78 80 88 d2 02 00"))
    assert info.classic_mac == "C8:24:78:80:88:D2"
    assert info.protocol_version == 2
    assert info.encrypted is False
    assert (
        protocol.parse_manufacturer_data(h("c8 24 78 80 88 d2 02 10")).encrypted is True
    )
    assert protocol.parse_manufacturer_data(None).classic_mac is None
