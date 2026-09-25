"""
Pagina web di diagnostica (solo libreria standard, funziona offline).

Un thread HTTP in ascolto su 127.0.0.1 serve `static/index.html` e l'ultimo
stato in JSON su /api/stato; il loop principale aggiorna lo stato con
publish(). La pagina è solo di visualizzazione: non comanda nulla.
"""

import json
import logging
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zm_packet import ZmPacket
from zone_logic import (FAULT_BAD_PACKETS, FAULT_NO_PACKETS, FAULT_PLC_HEARTBEAT, FAULT_SENSOR_TIMEOUT,
                        FAULT_ZONE_ERROR, FAULT_ZONE_NOT_LIVE, FAULT_ZONESET_HASH, Outputs)

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

FAULT_TEXT = {
    FAULT_NO_PACKETS: "Nessun pacchetto ricevuto dal sensore dall'avvio",
    FAULT_SENSOR_TIMEOUT: "Sensore muto (timeout pacchetti)",
    FAULT_ZONE_NOT_LIVE: "Una zona configurata non è tra le zone live del sensore",
    FAULT_ZONESET_HASH: "Set di zone diverso da quello atteso (hash)",
    FAULT_ZONE_ERROR: "Error flags attivi su una zona",
    FAULT_BAD_PACKETS: "Pacchetti scartati (CRC/formato) nell'ultimo secondo",
    FAULT_PLC_HEARTBEAT: "Heartbeat del PLC fermo",
}


def build_status(out: Outputs, pkt: ZmPacket | None, zones: list[tuple[str, int]],
                 plc: dict) -> dict:
    """Stato completo da mostrare nella pagina."""
    configured = []
    for idx, (name, zone_id) in enumerate(zones):
        zs = out.zone_details[idx]
        configured.append({
            "index": idx, "name": name, "zone_id": zone_id,
            "free": out.zone_free[idx], "triggered": out.zone_triggered[idx],
            "valid": out.zone_valid[idx], "live": zs is not None,
            "details": asdict(zs) if zs else None,
        })
    configured_ids = {zone_id for _, zone_id in zones}
    sensor = None
    if pkt is not None:
        sensor = {
            "serial": pkt.serial, "init_id": pkt.init_id, "frame_id": pkt.frame_id,
            "alert_flags": pkt.alert_flags, "zoneset_hash": pkt.zoneset_hash.hex(),
            "live_zones": [dict(asdict(z), configured=z.zone_id in configured_ids)
                           for z in sorted(pkt.zones.values(), key=lambda z: z.zone_id)],
        }
    return {
        "system_ok": out.system_ok, "sensor_ok": out.sensor_ok, "config_ok": out.config_ok,
        "fault_code": out.fault_code,
        "faults": [text for bit, text in FAULT_TEXT.items() if out.fault_code & bit],
        "packet_count": out.packet_count, "packet_age_ms": out.packet_age_ms,
        "zones": configured, "sensor": sensor, "plc": plc,
    }


class StatusServer:
    def __init__(self, port: int, host: str = "127.0.0.1"):
        self._lock = threading.Lock()
        self._payload = b"{}"
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, "text/html; charset=utf-8", INDEX_HTML.read_bytes())
                elif self.path == "/api/stato":
                    with server._lock:
                        body = server._payload
                    self._send(200, "application/json", body)
                else:
                    self._send(404, "text/plain", b"not found")

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):   # niente log per ogni richiesta
                pass

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._url = f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()
        logger.info("Pagina di diagnostica su %s", self._url)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def publish(self, status: dict) -> None:
        body = json.dumps(status).encode()
        with self._lock:
            self._payload = body
