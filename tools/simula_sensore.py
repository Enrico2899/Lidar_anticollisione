"""
Simulatore del sensore: invia pacchetti Zone Monitor finti via UDP, per
provare l'applicazione e la logica PLC al banco senza il LiDAR.

    python tools/simula_sensore.py --dest 127.0.0.1 --zone 0,1,2

Mentre gira, scrivere l'id di una zona + Invio per commutarla libera/occupata,
"s" + Invio per sospendere/riprendere l'invio (simula sensore scollegato).
"""

import argparse
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import zm_packet  # noqa: E402
from zm_packet import TRIGGER_TYPE_OCCUPANCY, ZoneState  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default="127.0.0.1", help="IP del PC con l'applicazione")
    parser.add_argument("--port", type=int, default=7504)
    parser.add_argument("--zone", default="0,1,2", help="id delle zone live, separati da virgola")
    parser.add_argument("--hz", type=float, default=10.0, help="frame al secondo del sensore")
    parser.add_argument("--serial", type=int, default=1)
    args = parser.parse_args()

    zone_ids = [int(z) for z in args.zone.split(",") if z.strip()]
    occupied: set[int] = set()
    paused = threading.Event()

    def read_commands():
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "s":
                if paused.is_set():
                    paused.clear()
                else:
                    paused.set()
                print("invio", "SOSPESO" if paused.is_set() else "RIPRESO")
            elif cmd.isdigit() and int(cmd) in zone_ids:
                occupied.symmetric_difference_update({int(cmd)})
                print("zone occupate:", sorted(occupied))

    threading.Thread(target=read_commands, daemon=True).start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Invio a {args.dest}:{args.port}, zone {zone_ids}. Id zona + Invio = commuta, s = pausa")

    frame_id = 0
    triggered_frames = {z: 0 for z in zone_ids}
    while True:
        if not paused.is_set():
            zones = []
            for z in zone_ids:
                occ = z in occupied
                triggered_frames[z] = triggered_frames[z] + 1 if occ else 0
                zones.append(ZoneState(
                    zone_id=z, error_flags=0, trigger_type=TRIGGER_TYPE_OCCUPANCY, triggered=occ,
                    triggered_frames=triggered_frames[z], point_count=500 if occ else 0,
                    occlusion_count=0, invalid_count=0, max_count=10000,
                    min_range_mm=4000 if occ else 0, max_range_mm=4500 if occ else 0,
                    mean_range_mm=4200 if occ else 0))
            sock.sendto(zm_packet.build(zones, frame_id=frame_id, serial=args.serial,
                                        timestamp_ns=time.time_ns()), (args.dest, args.port))
            frame_id = (frame_id + 1) & 0xFFFF
        time.sleep(1.0 / args.hz)


if __name__ == "__main__":
    main()
