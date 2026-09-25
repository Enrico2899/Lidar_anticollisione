"""
Parsing dei pacchetti UDP "Zone Monitor" dei sensori Ouster (firmware >= 3.2).

Layout (680 byte, tutti i campi little-endian), ricavato da ouster-sdk
(ouster_core/src/parsing.cpp) e verificato su una cattura reale OS0-128 fw 3.2:

    0   header (32 byte)
        0   u16   packet_type (3 = Zone Monitor)
        2   u16   frame_id
        4   u24   init_id (cambia a ogni riavvio del sensore)
        7   u40   numero di serie del sensore
        12  u8    alert_flags
    32  u64   timestamp [ns]
    40  32B   hash del set di zone live
    72  16 x 36 byte, uno slot per zona live:
        +0  bit 0      live (1 = slot usato)
        +1  u8         id della zona (come definito nell'app di configurazione)
        +2  u8         error_flags (dati mancanti, bassa confidenza, zona oscurata)
        +3  bit 2..3   trigger_type (1 = occupancy, 2 = vacancy)
        +3  bit 7      trigger_status (1 = allarme della zona attivo)
        +4  u32        triggered_frames (frame consecutivi in allarme)
        +8  u32        count (punti nella zona)
        +12 u32        occlusion_count
        +16 u32        invalid_count
        +20 u32        max_count
        +24 u32 (19b)  min_range [mm]
        +28 u32 (19b)  max_range [mm]
        +32 u32 (19b)  mean_range [mm]
    648 footer (32 byte), ultimi 8 byte = CRC64 (ECMA-182) dei byte precedenti
"""

import struct
from dataclasses import dataclass

ZM_PACKET_TYPE = 3
ZM_PACKET_SIZE = 680
ZM_MAX_LIVE_ZONES = 16

_ZONES_OFFSET = 72
_ZONE_SIZE = 36
_RANGE_MASK = (1 << 19) - 1

TRIGGER_TYPE_OCCUPANCY = 1
TRIGGER_TYPE_VACANCY = 2

_ZONE_STRUCT = struct.Struct("<BBBB8I")


class ZmPacketError(ValueError):
    """Pacchetto non valido (dimensione, tipo o CRC errati)."""


@dataclass(frozen=True)
class ZoneState:
    zone_id: int
    error_flags: int
    trigger_type: int
    triggered: bool           # trigger_status: la zona è in allarme
    triggered_frames: int
    point_count: int
    occlusion_count: int
    invalid_count: int
    max_count: int
    min_range_mm: int
    max_range_mm: int
    mean_range_mm: int


@dataclass(frozen=True)
class ZmPacket:
    frame_id: int
    init_id: int
    serial: int
    alert_flags: int
    timestamp_ns: int
    zoneset_hash: bytes
    zones: dict[int, ZoneState]   # solo gli slot "live", indicizzati per zone_id


def _make_crc64_table() -> list[int]:
    poly = 0xC96C5795D7870F42
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
        table.append(crc)
    return table


_CRC64_TABLE = _make_crc64_table()


def crc64(data: bytes) -> int:
    """CRC64 ECMA-182 (variante LSB-first) usato dal sensore nel footer."""
    crc = 0xFFFFFFFFFFFFFFFF
    for b in data:
        crc = _CRC64_TABLE[(b ^ crc) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFFFFFFFFFF


def parse(buf: bytes, verify_crc: bool = True) -> ZmPacket:
    if len(buf) != ZM_PACKET_SIZE:
        raise ZmPacketError(f"dimensione {len(buf)} byte, attesi {ZM_PACKET_SIZE}")

    packet_type, frame_id = struct.unpack_from("<HH", buf, 0)
    if packet_type != ZM_PACKET_TYPE:
        raise ZmPacketError(f"packet_type {packet_type}, atteso {ZM_PACKET_TYPE}")

    if verify_crc:
        expected = struct.unpack_from("<Q", buf, ZM_PACKET_SIZE - 8)[0]
        if crc64(buf[:-8]) != expected:
            raise ZmPacketError("CRC errato")

    init_id = int.from_bytes(buf[4:7], "little")
    serial = int.from_bytes(buf[7:12], "little")
    alert_flags = buf[12]
    timestamp_ns = struct.unpack_from("<Q", buf, 32)[0]
    zoneset_hash = bytes(buf[40:72])

    zones: dict[int, ZoneState] = {}
    for i in range(ZM_MAX_LIVE_ZONES):
        (b_live, zone_id, error_flags, b_trigger, triggered_frames, count,
         occlusion, invalid, max_count, min_r, max_r, mean_r) = _ZONE_STRUCT.unpack_from(
            buf, _ZONES_OFFSET + i * _ZONE_SIZE)
        if not b_live & 0x01:
            continue
        zones[zone_id] = ZoneState(
            zone_id=zone_id,
            error_flags=error_flags,
            trigger_type=(b_trigger >> 2) & 0x03,
            triggered=bool(b_trigger & 0x80),
            triggered_frames=triggered_frames,
            point_count=count,
            occlusion_count=occlusion,
            invalid_count=invalid,
            max_count=max_count,
            min_range_mm=min_r & _RANGE_MASK,
            max_range_mm=max_r & _RANGE_MASK,
            mean_range_mm=mean_r & _RANGE_MASK,
        )

    return ZmPacket(frame_id, init_id, serial, alert_flags, timestamp_ns, zoneset_hash, zones)


def build(zones: list[ZoneState], frame_id: int = 0, init_id: int = 1, serial: int = 1,
          zoneset_hash: bytes = bytes(32), timestamp_ns: int = 0) -> bytes:
    """
    Costruisce un pacchetto Zone Monitor valido (CRC incluso). Serve per i
    test e per il simulatore (tools/simula_sensore.py), non per il sensore reale.
    """
    buf = bytearray(ZM_PACKET_SIZE)
    struct.pack_into("<HH", buf, 0, ZM_PACKET_TYPE, frame_id & 0xFFFF)
    buf[4:7] = init_id.to_bytes(3, "little")
    buf[7:12] = serial.to_bytes(5, "little")
    struct.pack_into("<Q", buf, 32, timestamp_ns)
    buf[40:72] = zoneset_hash
    for i in range(ZM_MAX_LIVE_ZONES):
        off = _ZONES_OFFSET + i * _ZONE_SIZE
        if i < len(zones):
            z = zones[i]
            b_trigger = ((z.trigger_type & 0x03) << 2) | (0x80 if z.triggered else 0)
            _ZONE_STRUCT.pack_into(
                buf, off, 1, z.zone_id, z.error_flags, b_trigger, z.triggered_frames,
                z.point_count, z.occlusion_count, z.invalid_count, z.max_count,
                z.min_range_mm, z.max_range_mm, z.mean_range_mm)
        else:
            buf[off + 1] = 0xFF   # come il sensore negli slot vuoti
    struct.pack_into("<Q", buf, ZM_PACKET_SIZE - 8, crc64(bytes(buf[:-8])))
    return bytes(buf)
