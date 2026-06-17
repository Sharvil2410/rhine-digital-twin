"""
Extract Germany + Netherlands borders from the Natural Earth 50m admin-0 file
and write a small, Douglas-Peucker-simplified GeoJSON for the dashboard:
    dashboard/data/borders.geojson   (window-friendly country outlines)

One-off: download ne_50m_admin_0_countries.geojson into data/_src/ first
(see the download step in the build notes), then run this.
"""

import json
from pathlib import Path

SRC = Path(__file__).resolve().parent / "data" / "_src" / "ne_50m.geojson"
OUT = Path(__file__).resolve().parent / "data" / "borders.geojson"
WANT = {"Germany", "Netherlands"}
TOL = 0.008          # ~0.9 km Douglas-Peucker tolerance
MIN_RING_PTS = 8     # drop tiny islands that simplify to slivers


def perp_dist(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px, py = x1 + t * dx, y1 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def dp(points, tol):
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        dmax, idx = 0.0, -1
        for k in range(i + 1, j):
            d = perp_dist(points[k], points[i], points[j])
            if d > dmax:
                dmax, idx = d, k
        if dmax > tol and idx != -1:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def round_ring(ring):
    return [[round(x, 4), round(y, 4)] for x, y in ring]


def simplify_ring(ring):
    s = dp(ring, TOL)
    if s[0] != s[-1]:
        s.append(s[0])
    return round_ring(s) if len(s) >= 4 else round_ring(ring)


def simplify_to_lines(geom):
    # Emit border rings as MultiLineString: ground-clamped polygon OUTLINES are
    # not rendered by Cesium, but clamped polylines are, so we ship lines.
    t, c = geom["type"], geom["coordinates"]
    rings = []
    if t == "Polygon":
        for r in c:
            if len(r) >= MIN_RING_PTS:
                rings.append(simplify_ring(r))
    elif t == "MultiPolygon":
        for poly in c:
            for r in poly:
                if len(r) >= MIN_RING_PTS:
                    rings.append(simplify_ring(r))
    return {"type": "MultiLineString", "coordinates": rings} if rings else None


def npts(c):
    return len(c) if isinstance(c[0][0], (int, float)) else sum(npts(x) for x in c)


def main():
    src = json.load(open(SRC, encoding="utf-8"))
    feats = []
    for f in src["features"]:
        p = f.get("properties", {})
        name = p.get("ADMIN") or p.get("admin") or p.get("NAME") or p.get("name")
        if name in WANT:
            g = simplify_to_lines(f["geometry"])
            if g:
                feats.append({"type": "Feature", "properties": {"name": name},
                              "geometry": g})
                print(f"{name}: {npts(g['coordinates'])} pts")
    out = {"type": "FeatureCollection", "features": feats}
    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
