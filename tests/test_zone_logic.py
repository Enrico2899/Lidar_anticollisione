import zm_packet
from zm_packet import ZoneState
from zone_logic import (FAULT_BAD_PACKETS, FAULT_NO_PACKETS, FAULT_SENSOR_TIMEOUT, FAULT_ZONE_ERROR,
                        FAULT_ZONE_NOT_LIVE, FAULT_ZONESET_HASH, ZoneEvaluator)

ZONES = [("SX", 0), ("CENTRO", 1), ("DX", 2)]


def zone(zone_id, triggered=False, error_flags=0):
    return ZoneState(zone_id, error_flags, 1, triggered, 0, 0, 0, 0, 0, 0, 0, 0)


def packet(*zones, serial=1, zoneset_hash=bytes(32)):
    return zm_packet.parse(zm_packet.build(list(zones), serial=serial, zoneset_hash=zoneset_hash))


def make(**kw):
    return ZoneEvaluator(ZONES, sensor_timeout_s=0.3, **kw)


def test_no_packets_everything_blocked():
    out = make().evaluate(0.0)
    assert out.zone_free[:3] == [False] * 3
    assert not out.system_ok and not out.sensor_ok
    assert out.fault_code & FAULT_NO_PACKETS


def test_all_free():
    ev = make()
    ev.on_packet(packet(zone(0), zone(1), zone(2)), 0.0)
    out = ev.evaluate(0.05)
    assert out.zone_free == [True, True, True] + [False] * 13
    assert out.system_ok and out.fault_code == 0


def test_triggered_zone_not_free():
    ev = make()
    ev.on_packet(packet(zone(0), zone(1, triggered=True), zone(2)), 0.0)
    out = ev.evaluate(0.0)
    assert out.zone_free[:3] == [True, False, True]
    assert out.zone_triggered[:3] == [False, True, False]
    assert out.system_ok


def test_mapping_by_zone_id_not_slot():
    # il sensore può mettere le zone negli slot in un ordine qualsiasi
    ev = make()
    ev.on_packet(packet(zone(2, triggered=True), zone(0), zone(1)), 0.0)
    assert ev.evaluate(0.0).zone_free[:3] == [True, True, False]


def test_sensor_timeout_blocks_all():
    ev = make()
    ev.on_packet(packet(zone(0), zone(1), zone(2)), 0.0)
    out = ev.evaluate(0.31)
    assert out.zone_free[:3] == [False] * 3
    assert not out.sensor_ok and out.fault_code & FAULT_SENSOR_TIMEOUT


def test_missing_zone_blocks_only_that_zone_and_config():
    ev = make()
    ev.on_packet(packet(zone(0), zone(1)), 0.0)
    out = ev.evaluate(0.0)
    assert out.zone_free[:3] == [True, True, False]
    assert not out.config_ok and not out.system_ok
    assert out.fault_code & FAULT_ZONE_NOT_LIVE


def test_error_flags_block():
    ev = make()
    ev.on_packet(packet(zone(0, error_flags=4), zone(1), zone(2)), 0.0)
    out = ev.evaluate(0.0)
    assert out.zone_free[:3] == [False, True, True]
    assert out.fault_code & FAULT_ZONE_ERROR

    ev = make(error_flags_block=False)
    ev.on_packet(packet(zone(0, error_flags=4), zone(1), zone(2)), 0.0)
    assert ev.evaluate(0.0).zone_free[0] is True


def test_zoneset_hash_mismatch():
    ev = make(expected_hash="11" * 32)
    ev.on_packet(packet(zone(0), zone(1), zone(2), zoneset_hash=b"\x22" * 32), 0.0)
    out = ev.evaluate(0.0)
    assert out.zone_free[:3] == [False] * 3
    assert not out.config_ok and out.fault_code & FAULT_ZONESET_HASH

    ev = make(expected_hash="22" * 32)
    ev.on_packet(packet(zone(0), zone(1), zone(2), zoneset_hash=b"\x22" * 32), 0.0)
    assert ev.evaluate(0.0).system_ok


def test_other_sensor_ignored():
    ev = make(sensor_serial=42)
    ev.on_packet(packet(zone(0), zone(1), zone(2), serial=99), 0.0)
    assert ev.evaluate(0.0).fault_code & FAULT_NO_PACKETS


def test_bad_packets_flag_expires():
    ev = make()
    ev.on_packet(packet(zone(0), zone(1), zone(2)), 0.0)
    ev.on_bad_packet(0.0)
    assert ev.evaluate(0.1).fault_code & FAULT_BAD_PACKETS
    ev.on_packet(packet(zone(0), zone(1), zone(2)), 1.1)
    assert ev.evaluate(1.1).fault_code == 0
