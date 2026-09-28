"""
Verifica che i sorgenti TIA in plc/ producano gli stessi offset usati da
plc_link.py (regole di allineamento dei DB non ottimizzati S7).
"""

import re
from pathlib import Path

import plc_link

PLC_DIR = Path(__file__).parent.parent / "plc"
_FIELD = re.compile(r"^\s*(\w+)\s*:\s*([^;]+);")
_SIZES = {"Byte": 1, "USInt": 1, "SInt": 1, "Char": 1, "Int": 2, "UInt": 2, "Word": 2,
          "DInt": 4, "UDInt": 4, "DWord": 4, "Real": 4, "Time": 4}


def _fields(path: Path) -> list[tuple[str, str]]:
    body = path.read_text().split("STRUCT", 1)[1].split("END_STRUCT", 1)[0]
    return [(m.group(1), m.group(2).strip()) for line in body.splitlines() if (m := _FIELD.match(line))]


def _layout(fields, udts) -> tuple[dict[str, float], int]:
    """Ritorna {nome: offset (byte.bit come float)} e la dimensione in byte."""
    offsets, byte, bit = {}, 0, 0

    def align_even():
        nonlocal byte, bit
        if bit:
            byte, bit = byte + 1, 0
        byte += byte % 2

    for name, typ in fields:
        if typ == "Bool":
            offsets[name] = byte + bit / 10
            bit += 1
            if bit == 8:
                byte, bit = byte + 1, 0
            continue
        arr = re.match(r"Array\[0\.\.(\d+)\] of (.+)", typ)
        if arr:
            n, elem = int(arr.group(1)) + 1, arr.group(2).strip()
            align_even()
            offsets[name] = byte
            if elem == "Bool":
                size = (n + 7) // 8
            else:
                size = n * udts[elem.strip('"')]
            byte += size + size % 2
            continue
        if bit:
            byte, bit = byte + 1, 0
        size = _SIZES[typ] if typ in _SIZES else udts[typ.strip('"')]
        if size > 1:
            byte += byte % 2
        offsets[name] = byte
        byte += size
    if bit:
        byte += 1
    return offsets, byte + byte % 2


def test_udt_size():
    _, size = _layout(_fields(PLC_DIR / "LIDAR_ZoneData.udt"), {})
    assert size == plc_link.ZONE_DATA_SIZE


def test_db_offsets_match_plc_link():
    _, udt_size = _layout(_fields(PLC_DIR / "LIDAR_ZoneData.udt"), {})
    offsets, size = _layout(_fields(PLC_DIR / "LIDAR_DB.db"), {"LIDAR_ZoneData": udt_size})
    assert size == plc_link.DB_SIZE
    assert offsets == {
        "PLC_Heartbeat": 0, "PC_Heartbeat": 4,
        "System_OK": 8.0, "Sensor_OK": 8.1, "Config_OK": 8.2, "PLC_Heartbeat_OK": 8.3,
        "Sensor_Alert_Flags": 9, "Zone_Free": 10, "Zone_Triggered": 12, "Zone_Valid": 14,
        "ZM_Packet_Count": 16, "Sensor_Frame_Id": 20, "Packet_Age_ms": 24, "Fault_Code": 26,
        "Zone": plc_link.ZONE_DATA_OFFSET,
    }
