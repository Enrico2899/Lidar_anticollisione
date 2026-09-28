"""
Crea lo zone set (file .zip da caricare sul sensore) a partire da zone a forma
di parallelepipedo definite qui sotto, senza bisogno di un CAD.

Genera, nella cartella indicata (default: zone_set/):
    0.stl, 1.stl, ...        geometria di ogni zona (metri)
    zone_set.zip             zone set completo (metadati + STL), da caricare
                             dalla pagina web del sensore o con --upload

Richiede la libreria ufficiale Ouster:  pip install ouster-sdk

    python tools/crea_zone.py
    python tools/crea_zone.py --upload 172.16.20.154     # carica e applica sul sensore

Coordinate nel sistema di riferimento del SENSORE (metri), origine al centro
del sensore: +X esce dal lato OPPOSTO al cavo, +Y a sinistra guardando verso
+X, +Z verso l'alto (lato con il logo/coperchio). Verificare l'orientamento
con la nuvola di punti in Ouster Studio.

Vincoli del sensore: la zona deve essere nel campo visivo (OS1: ±22.5° in
verticale) e NON deve contenere il sensore; `point_count` non deve superare
il numero di raggi che attraversano la zona.
"""

import argparse
import os
from dataclasses import dataclass

# --- ZONE (modificare qui) ---------------------------------------------------


@dataclass
class BoxZone:
    zone_id: int
    label: str
    x: tuple[float, float]   # [m] min, max
    y: tuple[float, float]
    z: tuple[float, float]
    point_count: int = 50    # punti minimi nella zona per far scattare l'allarme
    frame_count: int = 2     # frame consecutivi (a 20 Hz: 2 frame = 100 ms)


# Zone di PROVA: due volumi a 1-4 m dal sensore, uno per lato, alti da 0.7 m
# sotto a 1.5 m sopra il sensore (con sensore a ~1 m da terra il pavimento
# resta fuori). Entrarci con una persona per vederle scattare.
ZONES = [
    BoxZone(0, "davanti", x=(1.0, 4.0), y=(-1.5, 1.5), z=(-0.7, 1.5)),
    BoxZone(1, "dietro", x=(-4.0, -1.0), y=(-1.5, 1.5), z=(-0.7, 1.5)),
]

# -----------------------------------------------------------------------------


def box_stl(zone: BoxZone) -> str:
    """STL ASCII di un parallelepipedo, triangoli con normali verso l'esterno."""
    (x0, x1), (y0, y1), (z0, z1) = zone.x, zone.y, zone.z
    if not (x0 < x1 and y0 < y1 and z0 < z1):
        raise ValueError(f"zona {zone.zone_id}: min deve essere < max su ogni asse")
    if x0 <= 0 <= x1 and y0 <= 0 <= y1 and z0 <= 0 <= z1:
        raise ValueError(f"zona {zone.zone_id}: la zona non può contenere il sensore (origine)")
    v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    # indici: bit2 = x, bit1 = y, bit0 = z
    faces = [
        ((-1, 0, 0), (0, 1, 3, 2)), ((1, 0, 0), (4, 6, 7, 5)),
        ((0, -1, 0), (0, 4, 5, 1)), ((0, 1, 0), (2, 3, 7, 6)),
        ((0, 0, -1), (0, 2, 6, 4)), ((0, 0, 1), (1, 5, 7, 3)),
    ]
    lines = [f"solid zona_{zone.zone_id}"]
    for n, (a, b, c, d) in faces:
        for tri in ((a, b, c), (a, c, d)):
            lines.append(f"  facet normal {n[0]} {n[1]} {n[2]}")
            lines.append("    outer loop")
            lines.extend(f"      vertex {v[i][0]:.4f} {v[i][1]:.4f} {v[i][2]:.4f}" for i in tri)
            lines.append("    endloop")
            lines.append("  endfacet")
    lines.append(f"endsolid zona_{zone.zone_id}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="zone_set", help="cartella di uscita")
    parser.add_argument("--upload", metavar="IP_SENSORE",
                        help="carica e applica lo zone set sul sensore (poi reinizializza)")
    args = parser.parse_args()

    import numpy as np
    from ouster.sdk import core

    os.makedirs(args.out, exist_ok=True)
    zone_set = core.ZoneSet()
    zone_set.sensor_to_body_transform = np.eye(4)   # zone nel sistema del sensore
    zones = {}
    for bz in ZONES:
        stl_path = os.path.join(args.out, f"{bz.zone_id}.stl")
        with open(stl_path, "w") as f:
            f.write(box_stl(bz))
        stl = core.Stl(stl_path)
        stl.coordinate_frame = core.CoordinateFrame.BODY
        zone = core.Zone()
        zone.stl = stl
        zone.point_count = bz.point_count
        zone.frame_count = bz.frame_count
        zone.mode = core.ZoneMode.OCCUPANCY
        zone.label = bz.label
        zones[bz.zone_id] = zone
        print(f"Zona {bz.zone_id} '{bz.label}': x={bz.x} y={bz.y} z={bz.z} -> {stl_path}")
    zone_set.zones = zones
    zone_set.power_on_live_ids = [bz.zone_id for bz in ZONES]

    zip_path = os.path.join(args.out, "zone_set.zip")
    zone_set.save(zip_path, core.ZoneSetOutputFilter.STL)
    print(f"Zone set salvato in {zip_path}")

    if args.upload:
        from ouster.sdk import sensor
        http = sensor.SensorHttp.create(args.upload)
        print(f"Caricamento su {args.upload}...")
        http.set_zone_monitor_config_zip(zone_set.to_zip_blob(core.ZoneSetOutputFilter.STL))
        http.apply_zone_monitor_staged_config_to_active()
        http.set_zone_monitor_live_ids([bz.zone_id for bz in ZONES])
        print("Reinizializzazione del sensore (qualche decina di secondi)...")
        http.reinitialize()
        print("Fatto. Zone live:", list(http.get_zone_monitor_live_ids()))


if __name__ == "__main__":
    main()
