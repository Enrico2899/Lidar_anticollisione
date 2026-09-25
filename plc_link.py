"""
Scrittura dello stato zone nel DB del PLC S7-1500 (python-snap7) e lettura
dell'heartbeat del PLC.

Il DB deve essere NON ottimizzato (accesso standard) e sulla CPU deve essere
abilitato "Consenti accesso PUT/GET". Layout (offset in byte.bit, S7 big-endian):

  --- PLC -> PC (scritto SOLO dal PLC) ---
  0.0    PLC_Heartbeat      DInt    incrementato dal PLC (es. ogni 100 ms)
  --- PC -> PLC (scritto SOLO dal PC) ---
  4.0    PC_Heartbeat       DInt    incrementato dal PC a ogni scrittura
  8.0    System_OK          Bool    Sensor_OK AND Config_OK
  8.1    Sensor_OK          Bool    pacchetti Zone Monitor freschi
  8.2    Config_OK          Bool    zone configurate tutte live (+ hash ok)
  8.3    PLC_Heartbeat_OK   Bool    il PC vede cambiare PLC_Heartbeat
  9.0    Sensor_Alert_Flags Byte    alert_flags dall'header del pacchetto
  10.0   Zone_Free          Array[0..15] of Bool  1 = libera e valida (fail-safe)
  12.0   Zone_Triggered     Array[0..15] of Bool  1 = allarme zona (diagnostica)
  14.0   Zone_Valid         Array[0..15] of Bool  1 = misura della zona valida
  16.0   ZM_Packet_Count    DInt
  20.0   Sensor_Frame_Id    DInt
  24.0   Packet_Age_ms      Int
  26.0   Fault_Code         Word    bit definiti in zone_logic.FAULT_*
  28.0   Zone               Array[0..15] of "ZoneData" (32 byte l'uno):
         +0  Zone_Id            USInt
         +1  Error_Flags        Byte
         +2  Trigger_Type       USInt   (1 = occupancy, 2 = vacancy)
         +3  Trigger_Status     USInt   (1 = allarme)
         +4  Triggered_Frames   UDInt
         +8  Point_Count        UDInt
         +12 Occlusion_Count    UDInt
         +16 Invalid_Count      UDInt
         +20 Min_Range_mm       UDInt
         +24 Mean_Range_mm      UDInt
         +28 Max_Range_mm       UDInt
  540    (dimensione totale)
"""

import logging
import struct

import snap7
from snap7.error import S7Error

from zone_logic import MAX_ZONES, Outputs

logger = logging.getLogger(__name__)

PLC_TO_PC_OFFSET = 0
PLC_TO_PC_SIZE = 4
PC_TO_PLC_OFFSET = 4
ZONE_DATA_OFFSET = 28
ZONE_DATA_SIZE = 32
DB_SIZE = ZONE_DATA_OFFSET + MAX_ZONES * ZONE_DATA_SIZE   # 540

# Overhead di protocollo S7 per una richiesta di scrittura, da togliere alla PDU
_WRITE_OVERHEAD = 35


class PLCConnectionError(Exception):
    """Sollevata quando la connessione al PLC fallisce o cade."""


def _bool_array(values: list[bool]) -> bytes:
    """Array[0..15] of Bool in un DB non ottimizzato: elemento i -> byte i//8, bit i%8."""
    out = bytearray(2)
    for i, v in enumerate(values):
        if v:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def encode(out: Outputs, pc_heartbeat: int, plc_heartbeat_ok: bool) -> bytes:
    """Immagine dei byte da PC_TO_PLC_OFFSET a DB_SIZE."""
    buf = bytearray(DB_SIZE - PC_TO_PLC_OFFSET)
    status = (out.system_ok << 0) | (out.sensor_ok << 1) | (out.config_ok << 2) | (plc_heartbeat_ok << 3)
    struct.pack_into(">iBB", buf, 0, pc_heartbeat, status, out.alert_flags)
    buf[6:8] = _bool_array(out.zone_free)
    buf[8:10] = _bool_array(out.zone_triggered)
    buf[10:12] = _bool_array(out.zone_valid)
    struct.pack_into(">iihH", buf, 12, out.packet_count, out.frame_id, out.packet_age_ms, out.fault_code)
    for i, zs in enumerate(out.zone_details):
        if zs is None:
            continue
        struct.pack_into(
            ">BBBB7I", buf, ZONE_DATA_OFFSET - PC_TO_PLC_OFFSET + i * ZONE_DATA_SIZE,
            zs.zone_id, zs.error_flags, zs.trigger_type, int(zs.triggered),
            zs.triggered_frames, zs.point_count, zs.occlusion_count, zs.invalid_count,
            zs.min_range_mm, zs.mean_range_mm, zs.max_range_mm)
    return bytes(buf)


class PLCLink:
    def __init__(self, ip: str, rack: int, slot: int, db_number: int):
        self._ip = ip
        self._rack = rack
        self._slot = slot
        self._db = db_number
        self._client = snap7.client.Client()

    def connect(self) -> None:
        try:
            self._client.connect(self._ip, self._rack, self._slot)
        except (S7Error, RuntimeError, OSError) as exc:
            raise PLCConnectionError(f"Connessione al PLC {self._ip} fallita: {exc}") from exc
        if not self._client.get_connected():
            raise PLCConnectionError(f"Impossibile connettersi al PLC {self._ip}")
        logger.info("Connesso al PLC %s (rack=%s, slot=%s), DB%s",
                    self._ip, self._rack, self._slot, self._db)

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        except (S7Error, RuntimeError, OSError):
            pass

    def is_connected(self) -> bool:
        try:
            return self._client.get_connected()
        except (S7Error, RuntimeError, OSError):
            return False

    def read_plc_heartbeat(self) -> int:
        try:
            data = self._client.db_read(self._db, PLC_TO_PC_OFFSET, PLC_TO_PC_SIZE)
        except (S7Error, RuntimeError, OSError) as exc:
            raise PLCConnectionError(f"Lettura heartbeat PLC fallita: {exc}") from exc
        return struct.unpack(">i", data)[0]

    def write(self, image: bytes) -> None:
        """
        Scrive l'immagine a partire da PC_TO_PLC_OFFSET. Se non sta in una
        PDU, la spezza scrivendo per ULTIMO il blocco iniziale: così
        PC_Heartbeat e Zone_Free arrivano al PLC solo dopo i dettagli, e il
        PLC non vede mai un heartbeat nuovo con dati vecchi.
        """
        try:
            chunk = max(self._client.get_pdu_length() - _WRITE_OVERHEAD, 64)
        except (S7Error, RuntimeError, OSError):
            chunk = 200
        starts = list(range(0, len(image), chunk))
        try:
            for start in reversed(starts):
                self._client.db_write(self._db, PC_TO_PLC_OFFSET + start, bytearray(image[start:start + chunk]))
        except (S7Error, RuntimeError, OSError) as exc:
            raise PLCConnectionError(f"Scrittura DB{self._db} fallita: {exc}") from exc
