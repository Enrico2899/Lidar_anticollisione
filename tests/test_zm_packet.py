from pathlib import Path

import pytest

import zm_packet
from zm_packet import TRIGGER_TYPE_OCCUPANCY, ZmPacketError, ZoneState

# Pacchetto reale OS0-128, firmware 3.2 (dai test di ouster-sdk: imu_zm_no_lidar.pcap)
REAL_PACKET = bytes.fromhex((Path(__file__).parent / "data" / "zm_os0_128_fw3.2.hex").read_text().replace("\n", ""))


def test_parse_real_packet():
    pkt = zm_packet.parse(REAL_PACKET)
    assert pkt.frame_id == 30002
    assert pkt.init_id == 15079160
    assert pkt.serial == 122247000785
    assert pkt.zoneset_hash.hex().startswith("d2db66d0")
    assert sorted(pkt.zones) == [0, 1, 2, 3]
    z0 = pkt.zones[0]
    assert z0.trigger_type == TRIGGER_TYPE_OCCUPANCY
    assert z0.triggered is True
    assert z0.error_flags == 0
    assert z0.triggered_frames == 0x27532
    assert (z0.point_count, z0.occlusion_count, z0.invalid_count, z0.max_count) == (742, 369, 5182, 6883)
    assert (z0.min_range_mm, z0.max_range_mm, z0.mean_range_mm) == (817, 1454, 1161)


def test_crc_error_detected():
    corrupted = bytearray(REAL_PACKET)
    corrupted[80] ^= 0x01
    with pytest.raises(ZmPacketError, match="CRC"):
        zm_packet.parse(bytes(corrupted))
    zm_packet.parse(bytes(corrupted), verify_crc=False)


@pytest.mark.parametrize("data", [REAL_PACKET[:-1], b"", REAL_PACKET + b"\x00"])
def test_wrong_size(data):
    with pytest.raises(ZmPacketError):
        zm_packet.parse(data)


def test_wrong_packet_type():
    buf = bytearray(REAL_PACKET)
    buf[0] = 2   # pacchetto IMU
    with pytest.raises(ZmPacketError, match="packet_type"):
        zm_packet.parse(bytes(buf), verify_crc=False)


def test_build_roundtrip():
    z = ZoneState(7, 0x02, 2, False, 0, 10, 1, 2, 300, 1000, 2000, 1500)
    pkt = zm_packet.parse(zm_packet.build([z], frame_id=5, init_id=9, serial=1234, zoneset_hash=b"\xAB" * 32))
    assert pkt.zones == {7: z}
    assert (pkt.frame_id, pkt.init_id, pkt.serial, pkt.zoneset_hash) == (5, 9, 1234, b"\xAB" * 32)
