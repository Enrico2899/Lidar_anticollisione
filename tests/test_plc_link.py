import struct

import plc_link
from zm_packet import ZoneState
from zone_logic import MAX_ZONES, Outputs


def test_db_size():
    assert plc_link.DB_SIZE == 540


def test_encode_offsets():
    out = Outputs(system_ok=True, sensor_ok=True, config_ok=False, alert_flags=0x5A,
                  fault_code=0x0104, packet_count=1234, frame_id=777, packet_age_ms=45)
    out.zone_free[0] = True
    out.zone_free[9] = True
    out.zone_triggered[1] = True
    out.zone_valid[15] = True
    out.zone_details[2] = ZoneState(5, 0x03, 1, True, 10, 20, 30, 40, 50, 1000, 3000, 2000)

    # immagine completa del DB come la vedrebbe il PLC
    db = bytes(4) + plc_link.encode(out, pc_heartbeat=99, plc_heartbeat_ok=True)
    assert len(db) == plc_link.DB_SIZE
    assert struct.unpack_from(">i", db, 4)[0] == 99              # PC_Heartbeat
    assert db[8] == 0b1011                                        # 8.0 System, 8.1 Sensor, 8.3 PLC_HB
    assert db[9] == 0x5A
    assert (db[10], db[11]) == (0x01, 0x02)                       # Zone_Free[0] = 10.0, [9] = 11.1
    assert (db[12], db[13]) == (0x02, 0x00)                       # Zone_Triggered[1] = 12.1
    assert (db[14], db[15]) == (0x00, 0x80)                       # Zone_Valid[15] = 15.7
    assert struct.unpack_from(">iihH", db, 16) == (1234, 777, 45, 0x0104)
    base = plc_link.ZONE_DATA_OFFSET + 2 * plc_link.ZONE_DATA_SIZE
    # Zone_Id, Error_Flags, Trigger_Type, Trigger_Status, Triggered_Frames, Point_Count,
    # Occlusion_Count, Invalid_Count, Min_Range_mm, Mean_Range_mm, Max_Range_mm
    assert struct.unpack_from(">BBBB7I", db, base) == (5, 3, 1, 1, 10, 20, 30, 40, 1000, 2000, 3000)
    assert db[plc_link.ZONE_DATA_OFFSET:base] == bytes(2 * plc_link.ZONE_DATA_SIZE)


def test_default_outputs_are_all_zero():
    assert plc_link.encode(Outputs(), 0, False) == bytes(plc_link.DB_SIZE - 4)
    assert len(Outputs().zone_free) == MAX_ZONES
