"""
Anticollisione carroponte: Ouster OS0 (Zone Monitor) -> DB PLC S7-1500.

Loop unico:
  - riceve i pacchetti UDP Zone Monitor (uno per frame del sensore);
  - ogni PLC_WRITE_PERIOD_S valuta le zone e scrive il DB di scambio,
    incrementando PC_Heartbeat (anche se il sensore tace: in quel caso
    Sensor_OK e Zone_Free vanno a 0, il PLC deve fermare il movimento);
  - legge PLC_Heartbeat e verifica che cambi.

Avvio:  python main.py
"""

import logging
import logging.handlers
import select
import signal
import socket
import time

import config
import plc_link
import sensor_http
import zm_packet
from plc_link import PLCConnectionError, PLCLink
from zone_logic import FAULT_PLC_HEARTBEAT, Outputs, ZoneEvaluator

logger = logging.getLogger("lidar_anticollisione")


class HeartbeatMonitor:
    """True finché il valore osservato cambia almeno una volta ogni `timeout_s`."""

    def __init__(self, timeout_s: float):
        self._timeout = timeout_s
        self._last_value: int | None = None
        self._last_change: float | None = None

    def update(self, value: int, now: float) -> None:
        if value != self._last_value:
            self._last_value = value
            self._last_change = now

    def reset(self) -> None:
        self._last_value = None
        self._last_change = None

    def is_alive(self, now: float) -> bool:
        return self._last_change is not None and now - self._last_change <= self._timeout


class TransitionLogger:
    """Scrive nel log solo i cambi di stato (zone e diagnostica), non ogni ciclo."""

    def __init__(self, zone_names: list[str]):
        self._names = zone_names
        self._prev: Outputs | None = None
        self._prev_plc_hb: bool | None = None

    def update(self, out: Outputs, plc_hb_ok: bool) -> None:
        prev = self._prev
        for attr in ("sensor_ok", "config_ok"):
            if prev is None or getattr(prev, attr) != getattr(out, attr):
                level = logging.INFO if getattr(out, attr) else logging.WARNING
                logger.log(level, "%s = %s (fault_code=0x%04X)", attr, getattr(out, attr), out.fault_code)
        for i, name in enumerate(self._names):
            if prev is None or prev.zone_free[i] != out.zone_free[i]:
                zs = out.zone_details[i]
                detail = (f"punti={zs.point_count} min={zs.min_range_mm}mm err=0x{zs.error_flags:02X}"
                          if zs else "zona non presente")
                logger.info("Zona %s: %s (%s)", name, "LIBERA" if out.zone_free[i] else "OCCUPATA/NON VALIDA", detail)
        if plc_hb_ok != self._prev_plc_hb:
            level = logging.INFO if plc_hb_ok else logging.WARNING
            logger.log(level, "Heartbeat PLC %s", "OK" if plc_hb_ok else "FERMO")
        self._prev = out
        self._prev_plc_hb = plc_hb_ok


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def open_udp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((config.ZM_UDP_BIND_IP, config.ZM_UDP_PORT))
    sock.setblocking(False)
    logger.info("In ascolto pacchetti Zone Monitor su %s:%s", config.ZM_UDP_BIND_IP, config.ZM_UDP_PORT)
    return sock


def drain_socket(sock: socket.socket, evaluator: ZoneEvaluator, now: float) -> None:
    """Legge tutti i pacchetti in coda (conta solo l'ultimo stato)."""
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as exc:   # es. ICMP port unreachable su Windows
            logger.debug("recvfrom: %s", exc)
            return
        try:
            pkt = zm_packet.parse(data, verify_crc=config.VERIFY_CRC)
        except zm_packet.ZmPacketError as exc:
            evaluator.on_bad_packet(now)
            logger.warning("Pacchetto scartato da %s: %s", addr[0], exc)
            continue
        evaluator.on_packet(pkt, now)


def run() -> None:
    zone_names = [name for name, _ in config.ZONES]
    zone_ids = [zone_id for _, zone_id in config.ZONES]

    if config.SENSOR_HTTP_CHECK:
        sensor_http.check_sensor(config.SENSOR_HOST, config.ZM_UDP_PORT, zone_ids)

    evaluator = ZoneEvaluator(
        config.ZONES, config.SENSOR_TIMEOUT_S, config.SENSOR_SERIAL,
        config.EXPECTED_ZONESET_HASH, config.ERROR_FLAGS_BLOCK)
    plc = PLCLink(config.PLC_IP, config.PLC_RACK, config.PLC_SLOT, config.PLC_DB_NUMBER)
    plc_hb = HeartbeatMonitor(config.PLC_HEARTBEAT_TIMEOUT_S)
    transitions = TransitionLogger(zone_names)
    sock = open_udp_socket()

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    pc_heartbeat = 0
    next_write = time.monotonic()
    next_reconnect = 0.0

    try:
        while running:
            now = time.monotonic()
            ready, _, _ = select.select([sock], [], [], max(0.0, next_write - now))
            now = time.monotonic()
            if ready:
                drain_socket(sock, evaluator, now)
            if now < next_write:
                continue
            next_write += config.PLC_WRITE_PERIOD_S
            if next_write < now:   # in ritardo (es. dopo un timeout di rete): riallinea
                next_write = now + config.PLC_WRITE_PERIOD_S

            out = evaluator.evaluate(now)

            if not plc.is_connected():
                if now < next_reconnect:
                    transitions.update(out, False)
                    continue
                try:
                    plc.connect()
                    plc_hb.reset()
                except PLCConnectionError as exc:
                    logger.error("%s", exc)
                    next_reconnect = now + config.PLC_RECONNECT_DELAY_S
                    transitions.update(out, False)
                    continue

            try:
                plc_hb.update(plc.read_plc_heartbeat(), now)
                plc_hb_ok = plc_hb.is_alive(now)
                if not plc_hb_ok:
                    out.fault_code |= FAULT_PLC_HEARTBEAT
                pc_heartbeat = (pc_heartbeat + 1) % 2**31
                plc.write(plc_link.encode(out, pc_heartbeat, plc_hb_ok))
            except PLCConnectionError as exc:
                logger.error("%s -> riconnessione", exc)
                plc.disconnect()
                next_reconnect = time.monotonic() + config.PLC_RECONNECT_DELAY_S
                plc_hb_ok = False
            transitions.update(out, plc_hb_ok)
    finally:
        logger.info("Arresto: azzero le uscite nel DB e chiudo le connessioni")
        if plc.is_connected():
            try:
                # Zone_Free/System_OK a 0 subito, senza aspettare il watchdog del PLC
                plc.write(plc_link.encode(Outputs(), pc_heartbeat, False))
            except PLCConnectionError as exc:
                logger.error("%s", exc)
        sock.close()
        plc.disconnect()


if __name__ == "__main__":
    setup_logging()
    run()
