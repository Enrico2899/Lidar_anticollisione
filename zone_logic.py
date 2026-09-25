"""
Logica di valutazione delle zone, indipendente da rete e PLC (testabile).

Principio fail-safe: il PC comunica al PLC "Zone_Free" (1 = zona libera e
misura valida). Qualsiasi condizione anomala (sensore muto, zona non live,
set di zone modificato, errori sulla zona) porta il bit a 0, esattamente come
se ci fosse un ostacolo. Lo stesso vale per un DB mai scritto (valori a 0).
"""

import logging
from dataclasses import dataclass, field

from zm_packet import ZmPacket, ZoneState

logger = logging.getLogger(__name__)

MAX_ZONES = 16

# Bit di Fault_Code (Word nel DB) — cause per cui qualcosa non è OK
FAULT_NO_PACKETS = 1 << 0        # nessun pacchetto ricevuto dall'avvio
FAULT_SENSOR_TIMEOUT = 1 << 1    # pacchetti fermi da più di SENSOR_TIMEOUT_S
FAULT_ZONE_NOT_LIVE = 1 << 2     # una zona configurata non è tra le zone live
FAULT_ZONESET_HASH = 1 << 3      # hash del set di zone diverso da quello atteso
FAULT_ZONE_ERROR = 1 << 4        # una zona configurata ha error_flags != 0
FAULT_BAD_PACKETS = 1 << 5       # pacchetti scartati (CRC/formato) nell'ultimo secondo
FAULT_PLC_HEARTBEAT = 1 << 6     # il PC non vede cambiare PLC_Heartbeat


@dataclass
class Outputs:
    """Immagine di quello che va scritto nel DB del PLC."""
    system_ok: bool = False
    sensor_ok: bool = False
    config_ok: bool = False
    zone_free: list[bool] = field(default_factory=lambda: [False] * MAX_ZONES)
    zone_triggered: list[bool] = field(default_factory=lambda: [False] * MAX_ZONES)
    zone_valid: list[bool] = field(default_factory=lambda: [False] * MAX_ZONES)
    zone_details: list[ZoneState | None] = field(default_factory=lambda: [None] * MAX_ZONES)
    fault_code: int = 0
    packet_count: int = 0
    frame_id: int = 0
    packet_age_ms: int = 0
    alert_flags: int = 0


class ZoneEvaluator:
    def __init__(self, zones: list[tuple[str, int]], sensor_timeout_s: float,
                 sensor_serial: int | None = None, expected_hash: str | None = None,
                 error_flags_block: bool = True):
        if len(zones) > MAX_ZONES:
            raise ValueError(f"massimo {MAX_ZONES} zone")
        self._zones = zones
        self._timeout = sensor_timeout_s
        self._serial = sensor_serial
        self._expected_hash = bytes.fromhex(expected_hash) if expected_hash else None
        self._error_flags_block = error_flags_block

        self._last: ZmPacket | None = None
        self._last_time: float | None = None
        self._packet_count = 0
        self._bad_packet_times: list[float] = []

    @property
    def last_packet(self) -> ZmPacket | None:
        return self._last

    # --- ingresso dati ---

    def on_packet(self, pkt: ZmPacket, now: float) -> None:
        if self._serial is not None and pkt.serial != self._serial:
            logger.warning("Pacchetto dal sensore S/N %s ignorato (atteso %s)", pkt.serial, self._serial)
            return
        if self._last is None:
            logger.info("Primo pacchetto Zone Monitor: S/N %s, zone live %s, hash %s",
                        pkt.serial, sorted(pkt.zones), pkt.zoneset_hash.hex())
        elif pkt.init_id != self._last.init_id:
            logger.warning("init_id cambiato (%s -> %s): il sensore si è riavviato",
                           self._last.init_id, pkt.init_id)
        if self._last is not None and pkt.zoneset_hash != self._last.zoneset_hash:
            logger.warning("Hash del set di zone cambiato: %s", pkt.zoneset_hash.hex())
        self._last = pkt
        self._last_time = now
        self._packet_count = (self._packet_count + 1) % 2**31

    def on_bad_packet(self, now: float) -> None:
        self._bad_packet_times.append(now)

    # --- uscita verso il PLC ---

    def evaluate(self, now: float) -> Outputs:
        out = Outputs(packet_count=self._packet_count)
        fault = 0

        self._bad_packet_times = [t for t in self._bad_packet_times if now - t < 1.0]
        if self._bad_packet_times:
            fault |= FAULT_BAD_PACKETS

        pkt = self._last
        if pkt is None:
            fault |= FAULT_NO_PACKETS
            out.fault_code = fault
            return out

        age = now - self._last_time
        out.packet_age_ms = min(int(age * 1000), 32767)
        out.frame_id = pkt.frame_id
        out.alert_flags = pkt.alert_flags
        out.sensor_ok = age <= self._timeout
        if not out.sensor_ok:
            fault |= FAULT_SENSOR_TIMEOUT

        hash_ok = self._expected_hash is None or pkt.zoneset_hash == self._expected_hash
        if not hash_ok:
            fault |= FAULT_ZONESET_HASH

        all_live = True
        for idx, (_name, zone_id) in enumerate(self._zones):
            zs = pkt.zones.get(zone_id)
            if zs is None:
                all_live = False
                fault |= FAULT_ZONE_NOT_LIVE
                continue
            out.zone_details[idx] = zs
            out.zone_triggered[idx] = zs.triggered
            has_error = zs.error_flags != 0
            if has_error:
                fault |= FAULT_ZONE_ERROR
            valid = out.sensor_ok and hash_ok and not (has_error and self._error_flags_block)
            out.zone_valid[idx] = valid
            # Occupancy: trigger = oggetto nella zona. Vacancy: trigger = zona
            # vuota quando dovrebbe essere piena. In entrambi i casi "trigger
            # attivo" = condizione di allarme -> movimento non consentito.
            out.zone_free[idx] = valid and not zs.triggered

        out.config_ok = hash_ok and all_live
        out.system_ok = out.sensor_ok and out.config_ok
        out.fault_code = fault
        return out
