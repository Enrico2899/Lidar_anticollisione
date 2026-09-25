import json
import urllib.request

import web_ui
import zm_packet
from zm_packet import ZoneState
from zone_logic import ZoneEvaluator

ZONES = [("SX", 0), ("DX", 2)]


def test_build_status_and_server():
    zones = [ZoneState(0, 0, 1, True, 3, 50, 0, 0, 0, 1200, 1500, 1300), ZoneState(7, 0, 1, False, 0, 0, 0, 0, 0, 0, 0, 0)]
    pkt = zm_packet.parse(zm_packet.build(zones))
    ev = ZoneEvaluator(ZONES, sensor_timeout_s=0.3)
    ev.on_packet(pkt, 0.0)
    st = web_ui.build_status(ev.evaluate(0.0), ev.last_packet, ZONES, {"enabled": False})

    assert [z["free"] for z in st["zones"]] == [False, False]
    assert st["zones"][0]["triggered"] and st["zones"][0]["details"]["min_range_mm"] == 1200
    assert st["zones"][1]["live"] is False           # zona 2 configurata ma non live
    assert [(z["zone_id"], z["configured"]) for z in st["sensor"]["live_zones"]] == [(0, True), (7, False)]
    assert st["faults"] == ["Una zona configurata non è tra le zone live del sensore"]

    srv = web_ui.StatusServer(0)
    port = srv._httpd.server_address[1]
    srv.start()
    try:
        srv.publish(st)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stato") as r:
            assert json.loads(r.read()) == st
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert b"Anticollisione LiDAR" in r.read()
    finally:
        srv.stop()
