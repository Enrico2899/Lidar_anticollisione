"""
Check diagnostico del sensore via API HTTP all'avvio (firmware, destinazione
UDP Zone Monitor, zone live). Non modifica la configurazione del sensore e non
è bloccante: segnala solo nel log quello che non torna.
"""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)


def _get_json(host: str, path: str, timeout: float):
    with urllib.request.urlopen(f"http://{host}/api/v1/{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def check_sensor(host: str, zm_port: int, zone_ids: list[int], timeout: float = 3.0) -> list[str]:
    """Ritorna la lista dei problemi trovati (vuota se tutto ok)."""
    problems: list[str] = []
    try:
        info = _get_json(host, "sensor/metadata/sensor_info", timeout)
        logger.info("Sensore %s: %s S/N %s, firmware %s", host, info.get("prod_line"),
                    info.get("prod_sn"), info.get("image_rev") or info.get("build_rev"))

        cfg = _get_json(host, "sensor/config", timeout)
        logger.info("Sensore: udp_dest_zm=%s udp_port_zm=%s",
                    cfg.get("udp_dest_zm"), cfg.get("udp_port_zm"))
        if "udp_port_zm" not in cfg:
            problems.append("il sensore non espone udp_port_zm: firmware senza Zone Monitor (serve >= 3.2)?")
        elif cfg.get("udp_port_zm") != zm_port:
            problems.append(f"udp_port_zm del sensore = {cfg.get('udp_port_zm')}, "
                            f"l'app ascolta su {zm_port}")
        if not cfg.get("udp_dest_zm"):
            problems.append("udp_dest_zm non impostato: il sensore non invia i pacchetti Zone Monitor")

        live_ids = _get_json(host, "zone_monitor/live_ids", timeout)
        logger.info("Sensore: zone live %s", live_ids)
        missing = [z for z in zone_ids if z not in live_ids]
        if missing:
            problems.append(f"zone configurate ma non live sul sensore: {missing}")
    except (OSError, ValueError) as exc:
        problems.append(f"check HTTP del sensore {host} non riuscito: {exc}")

    for p in problems:
        logger.warning("Check sensore: %s", p)
    return problems
