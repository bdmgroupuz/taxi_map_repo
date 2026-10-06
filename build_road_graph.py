#!/usr/bin/env python3
"""
O'zbekiston yo'llar grafini yasaydi — Tulpor Taxi Driver ilovasi telefonning o'zida
yo'l bo'yicha masofa hisoblashi uchun.

Kirish:  OpenStreetMap .osm.pbf fayli (masalan Geofabrik uzbekistan-latest.osm.pbf)
Chiqish: <out>.bin.gz (ilova yuklab oladigan graf) va <out>.json (versiya ma'lumoti)

Masofa uchun keraksiz hamma narsa tashlanadi: faqat avtomobil yo'llari, faqat chorrahalar
va yo'l shaklini saqlaydigan burilish nuqtalari qoladi (ilova nuqtani eng yaqin yo'l bo'lagiga bog'laydi).

Fayl formati (big-endian, ilovadagi RoadGraphIO.kt bilan bir xil bo'lishi SHART):
    int32  magic = 0x54524732 ("TRG2")
    int32  version = 2
    int64  created_at_ms
    int32  node_count (n), int32 edge_count (m)
    int32[n]   lat * 1e6        } tugunlar katak kaliti bo'yicha saralangan
    int32[n]   lng * 1e6        } (cell_key funksiyasiga qarang)
    int32[n+1] edge_start (CSR)
    int32[m]   edge_to
    float32[m] edge_meters

Ishlatish:  python build_road_graph.py uzbekistan-latest.osm.pbf uz_roads
"""
import array
import gzip
import hashlib
import json
import math
import struct
import sys
import time

import osmium

HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
SPACING_M = 1000.0       # to'g'ri yo'lda ham shundan uzoq bo'lmagan oraliqda nuqta qoladi (ilova bo'laklarni yaqin tugunlar orqali topadi)
SHAPE_TOLERANCE_M = 5.0  # tashlangan nuqtalar to'g'ri kesmadan shundan uzoqlashmasligi kerak (egri yo'l shakli saqlanadi)
MIN_COMPONENT = 50       # asosiy tarmoqqa ulanmagan mayda bo'laklar tashlanadi
MAGIC = 0x54524732
VERSION = 2
CELL_E6 = 10_000         # 0.01° — RoadGraph.kt dagi CELL_E6 bilan bir xil
COLS = 36_001
EARTH_RADIUS_M = 6_371_000.0

BOTH, FORWARD, REVERSE = 0, 1, -1


def direction(tags):
    """RoadGraphBuilder.direction() bilan bir xil qoida."""
    oneway = tags.get("oneway")
    if oneway in ("yes", "1", "true"):
        return FORWARD
    if oneway in ("-1", "reverse"):
        return REVERSE
    if oneway in ("no", "0", "false"):
        return BOTH
    if tags.get("junction") in ("roundabout", "circular") or tags.get("highway") == "motorway":
        return FORWARD
    return BOTH


def meters(lat1, lng1, lat2, lng2):
    """RoadGraph.metersBetween() bilan bir xil (equirectangular), kirish — gradus * 1e6."""
    a1, o1, a2, o2 = lat1 / 1e6, lng1 / 1e6, lat2 / 1e6, lng2 / 1e6
    x = math.radians(o2 - o1) * math.cos(math.radians((a1 + a2) / 2.0))
    y = math.radians(a2 - a1)
    return math.sqrt(x * x + y * y) * EARTH_RADIUS_M


def deviation(p, a, b):
    """p nuqtadan a-b kesmagacha masofa (metr), kirish — (lat_e6, lng_e6)."""
    k = math.cos(math.radians(p[0] / 1e6))
    ax, ay = (a[1] - p[1]) * k, a[0] - p[0]
    dx, dy = (b[1] - p[1]) * k - ax, b[0] - p[0] - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    return math.radians(math.hypot(ax + t * dx, ay + t * dy) / 1e6) * EARTH_RADIUS_M


def cell_key(lat_e6, lng_e6):
    return ((lat_e6 + 90_000_000) // CELL_E6) * COLS + (lng_e6 + 180_000_000) // CELL_E6


class Roads(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []      # (direction, [node_id...])
        self.coords = {}    # node_id -> (lat_e6, lng_e6)
        self.uses = {}      # node_id -> necha marta yo'llarda uchraydi

    def way(self, w):
        if w.tags.get("highway") not in HIGHWAYS:
            return
        refs = []
        for n in w.nodes:
            if not n.location.valid():
                continue
            refs.append(n.ref)
            if n.ref not in self.coords:
                self.coords[n.ref] = (round(n.location.lat * 1e6), round(n.location.lon * 1e6))
            self.uses[n.ref] = self.uses.get(n.ref, 0) + 1
        if len(refs) >= 2:
            self.ways.append((direction(w.tags), refs))


def build(pbf_path):
    roads = Roads()
    roads.apply_file(pbf_path, locations=True)
    coords, uses = roads.coords, roads.uses
    print(f"xom: {len(roads.ways)} yo'l, {len(coords)} nuqta", flush=True)

    # Qoladigan tugunlar: yo'l uchlari, chorrahalar (bir necha yo'lda uchraydigan), har SPACING_M da
    # bitta va yo'l burilgan joylar. Burilishlar shart: ilova nuqtani eng yaqin BO'LAKKA bog'laydi,
    # egri yo'l uzun to'g'ri kesma bilan almashtirilsa kesma qo'shni (masalan teskari yo'nalishdagi)
    # qatnov qismi ustidan o'tib, nuqta noto'g'ri yo'lga "yopishadi".
    edges = []  # (from_id, to_id, meters) — yo'naltirilgan
    kept = set()

    def emit(way_dir, a, b, length):
        kept.add(b)
        if a != b:
            if way_dir != REVERSE:
                edges.append((a, b, length))
            if way_dir != FORWARD:
                edges.append((b, a, length))

    for way_dir, refs in roads.ways:
        last = refs[0]
        kept.add(last)
        acc = 0.0        # last dan hozirgi nuqtagacha yo'l uzunligi
        skipped = []     # last dan keyin tashlab ketilayotgan nuqtalar
        for i in range(1, len(refs)):
            prev, cur = refs[i - 1], refs[i]
            step = meters(*coords[prev], *coords[cur])
            # cur gacha to'g'ri kesma tortilsa tashlangan nuqtalar shakldan chiqib ketadimi?
            if skipped and any(deviation(coords[p], coords[last], coords[cur]) > SHAPE_TOLERANCE_M for p in skipped):
                emit(way_dir, last, prev, acc)
                last, acc, skipped = prev, 0.0, []
            acc += step
            if i == len(refs) - 1 or uses[cur] > 1 or acc >= SPACING_M:
                emit(way_dir, last, cur, acc)
                last, acc, skipped = cur, 0.0, []
            else:
                skipped.append(cur)

    # Bog'langan qismlar (yo'nalishsiz) — mayda uzilgan bo'laklarni tashlash uchun
    parent = {n: n for n in kept}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b, _ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    size = {}
    for n in kept:
        r = find(n)
        size[r] = size.get(r, 0) + 1
    nodes = [n for n in kept if size[find(n)] >= MIN_COMPONENT]
    print(f"qoldi: {len(nodes)} tugun ({len(kept) - len(nodes)} tasi mayda bo'laklarda tashlandi)", flush=True)

    # Katak kaliti bo'yicha saralash — ilova eng yaqin tugunni qo'shimcha indekssiz topadi
    nodes.sort(key=lambda n: (cell_key(*coords[n]), n))
    index = {n: i for i, n in enumerate(nodes)}
    count = len(nodes)

    degree = [0] * (count + 1)
    directed = []
    for a, b, m in edges:
        ia, ib = index.get(a), index.get(b)
        if ia is None or ib is None:
            continue
        directed.append((ia, ib, m))
        degree[ia + 1] += 1
    for i in range(count):
        degree[i + 1] += degree[i]
    edge_to = array.array("i", bytes(4 * len(directed)))
    edge_m = array.array("f", bytes(4 * len(directed)))
    cursor = degree[:count]
    for ia, ib, m in directed:
        slot = cursor[ia]
        cursor[ia] = slot + 1
        edge_to[slot] = ib
        edge_m[slot] = m

    lat = array.array("i", (coords[n][0] for n in nodes))
    lng = array.array("i", (coords[n][1] for n in nodes))
    edge_start = array.array("i", degree)
    return lat, lng, edge_start, edge_to, edge_m


def big_endian(arr):
    if sys.byteorder == "little":
        arr = array.array(arr.typecode, arr)
        arr.byteswap()
    return arr.tobytes()


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pbf_path, out = sys.argv[1], sys.argv[2]
    started = time.time()
    lat, lng, edge_start, edge_to, edge_m = build(pbf_path)
    created_at_ms = int(time.time() * 1000)

    payload = b"".join([
        struct.pack(">iiqii", MAGIC, VERSION, created_at_ms, len(lat), len(edge_to)),
        big_endian(lat), big_endian(lng), big_endian(edge_start), big_endian(edge_to), big_endian(edge_m),
    ])
    packed = gzip.compress(payload, compresslevel=9, mtime=0)
    with open(out + ".bin.gz", "wb") as f:
        f.write(packed)
    meta = {
        "version": VERSION,
        "created_at_ms": created_at_ms,
        "nodes": len(lat),
        "edges": len(edge_to),
        "size": len(packed),
        "raw_size": len(payload),
        "sha256": hashlib.sha256(packed).hexdigest(),
    }
    with open(out + ".json", "w") as f:
        json.dump(meta, f)
    print(json.dumps(meta), f"({time.time() - started:.0f} s)")


if __name__ == "__main__":
    main()
