"""
Whole-reach hillshade tile pyramid for the dashboard (QGIS-level detail).

Stage 1 — stream data_raw/rhine_dem.tif (84 GB, 1 m, striped rows, no
overviews) once and write a 2 m hillshade GeoTIFF with overviews:
    data_processed/rhine_hillshade_2m.tif      (also usable in QGIS)

Stage 2 — cut a web-mercator XYZ tile pyramid (z8..z16, ~1.5 m/px ground
resolution at z16) from that hillshade, skipping tiles with no data:
    dashboard/data/dem_tiles/{z}/{x}/{y}.png

Finally the tile-source entry is merged into dashboard/data/dem_layers.js.
One-off build (~30 min, dominated by the 84 GB sequential read).
"""

import importlib.util
import json
import math
import os
import time
from pathlib import Path

_proj_data = Path(importlib.util.find_spec("rasterio").origin).parent / "proj_data"
if _proj_data.exists():
    os.environ.setdefault("PROJ_DATA", str(_proj_data))
    os.environ.setdefault("PROJ_LIB", str(_proj_data))

import numpy as np
import rasterio
from matplotlib.colors import LightSource
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEM_PATH = PROJECT_ROOT / "data_raw" / "rhine_dem.tif"
HS_PATH = PROJECT_ROOT / "data_processed" / "rhine_hillshade_2m.tif"
TILE_DIR = Path(__file__).resolve().parent / "data" / "dem_tiles"
META_PATH = Path(__file__).resolve().parent / "data" / "dem_layers.js"

CRS_UTM = CRS.from_epsg(25832)
CRS_WGS = CRS.from_epsg(4326)
CRS_MERC = CRS.from_epsg(3857)
NODATA_ELEV = 0.0
STEP = 2                      # m — hillshade grid
VERT_EXAG = 3
MIN_Z, MAX_Z = 8, 16
CHUNK = 512                   # output rows per write (matches tif block size)


def stage1_hillshade():
    ls = LightSource(azdeg=315, altdeg=45)
    t0 = time.time()
    with rasterio.open(DEM_PATH) as src:
        W, H = src.width, src.height
        ow, oh = W // STEP, H // STEP
        transform = Affine(STEP * src.transform.a, 0, src.transform.c,
                           0, STEP * src.transform.e, src.transform.f)
        profile = dict(driver="GTiff", width=ow, height=oh, count=1,
                       dtype="uint8", crs=CRS_UTM, transform=transform,
                       nodata=0, tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate", predictor=2, BIGTIFF="IF_SAFER")

        def sample_row(out_r):
            """Decimated row: every STEP-th source row, STEP-px column means."""
            r = min(out_r * STEP, H - 1)
            a = src.read(1, window=Window(0, r, W, 1))[0][:ow * STEP]
            a = a.reshape(ow, STEP).astype(np.float32)
            a[a <= NODATA_ELEV] = np.nan
            with np.errstate(invalid="ignore"):
                return np.nanmean(a, axis=1)

        with rasterio.open(HS_PATH, "w", **profile) as dst:
            prev_edge = None                       # last sampled row of previous chunk
            for cs in range(0, oh, CHUNK):
                ce = min(cs + CHUNK, oh)
                n = ce - cs
                rows = [sample_row(r) for r in range(cs, min(ce + 1, oh))]
                block = np.vstack(([prev_edge] if prev_edge is not None else [rows[0]])
                                  + rows)
                prev_edge = rows[n - 1]
                hs = ls.hillshade(block, vert_exag=VERT_EXAG, dx=STEP, dy=STEP)
                hs = np.nan_to_num(np.asarray(hs, dtype=np.float32), nan=0.0)
                out = np.where(np.isnan(block), 0, 1 + hs * 254).astype(np.uint8)
                dst.write(out[1:n + 1], 1, window=Window(0, cs, ow, n))
                if (cs // CHUNK) % 10 == 0:
                    el = time.time() - t0
                    print(f"  stage1 rows {ce}/{oh}  ({el/60:.1f} min)", flush=True)
            print("  building overviews...", flush=True)
            dst.build_overviews([2, 4, 8, 16, 32, 64, 128, 256], Resampling.average)
    print(f"  stage1 done in {(time.time()-t0)/60:.1f} min -> {HS_PATH.name} "
          f"({HS_PATH.stat().st_size/1e9:.2f} GB)", flush=True)


WORLD = 2 * math.pi * 6378137 / 2          # mercator half-size


def tile_merc_bounds(z, x, y):
    res = 2 * WORLD / 2 ** z
    return (-WORLD + x * res, WORLD - (y + 1) * res,
            -WORLD + (x + 1) * res, WORLD - y * res)


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    xt = int((lon + 180) / 360 * n)
    lr = math.radians(lat)
    yt = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)
    return max(0, min(xt, n - 1)), max(0, min(yt, n - 1))


def stage2_tiles():
    t0 = time.time()
    counts = {}
    with rasterio.open(HS_PATH) as src:
        wgs = transform_bounds(CRS_UTM, CRS_WGS, *src.bounds)
        # WarpedVRT forbids boundless reads, so give it an extent snapped to
        # the MIN_Z tile block plus a margin — every tile window then falls
        # inside. Resolution = exact z[MAX_Z] pixel size so max-zoom tiles
        # read 1:1 and lower zooms are clean power-of-two decimations.
        res = 2 * WORLD / 2 ** MAX_Z / 256
        x0, y0 = lonlat_to_tile(wgs[0], wgs[3], MIN_Z)
        x1, y1 = lonlat_to_tile(wgs[2], wgs[1], MIN_Z)
        nw = tile_merc_bounds(MIN_Z, x0, y0)
        se = tile_merc_bounds(MIN_Z, x1, y1)
        margin = 256 * res
        vrt_transform = Affine(res, 0, nw[0] - margin, 0, -res, nw[3] + margin)
        vrt_w = round((se[2] - nw[0] + 2 * margin) / res)
        vrt_h = round((nw[3] - se[1] + 2 * margin) / res)
        vrt = WarpedVRT(src, crs=CRS_MERC, resampling=Resampling.bilinear,
                        src_nodata=0, nodata=0, transform=vrt_transform,
                        width=vrt_w, height=vrt_h)

        def read_tile(z, x, y, size=256):
            w, s, e, n = tile_merc_bounds(z, x, y)
            win = vrt.window(w, s, e, n)
            return vrt.read(1, window=win, out_shape=(size, size))

        prev_set = None                      # data tiles of the previous level
        for z in range(MIN_Z, MAX_Z + 1):
            x0, y0 = lonlat_to_tile(wgs[0], wgs[3], z)
            x1, y1 = lonlat_to_tile(wgs[2], wgs[1], z)
            if prev_set is None:
                candidates = [(x, y) for x in range(x0, x1 + 1)
                              for y in range(y0, y1 + 1)]
            else:
                candidates = sorted({(px * 2 + dx, py * 2 + dy)
                                     for px, py in prev_set
                                     for dx in (0, 1) for dy in (0, 1)
                                     if x0 <= px * 2 + dx <= x1
                                     and y0 <= py * 2 + dy <= y1})
            cur = set()
            for x, y in candidates:
                arr = read_tile(z, x, y)
                if not arr.any():
                    continue
                cur.add((x, y))
                alpha = np.where(arr > 0, 255, 0).astype(np.uint8)
                rgba = np.dstack([arr, arr, arr, alpha])
                d = TILE_DIR / str(z) / str(x)
                d.mkdir(parents=True, exist_ok=True)
                Image.fromarray(rgba, "RGBA").save(d / f"{y}.png")
            prev_set = cur
            counts[z] = len(cur)
            print(f"  z{z}: {len(cur)} tiles ({len(candidates)} candidates, "
                  f"{(time.time()-t0)/60:.1f} min)", flush=True)

    total = sum(counts.values())
    size_mb = sum(f.stat().st_size for f in TILE_DIR.rglob("*.png")) / 1e6
    print(f"  stage2 done: {total} tiles, {size_mb:.0f} MB", flush=True)
    return [round(v, 6) for v in wgs]


def merge_meta(bounds):
    txt = META_PATH.read_text(encoding="utf-8")
    meta = json.loads(txt[txt.index("=") + 1:].rstrip().rstrip(";"))
    meta["tiles"] = {"url": "data/dem_tiles/{z}/{x}/{y}.png",
                     "minZoom": MIN_Z, "maxZoom": MAX_Z, "bounds": bounds}
    META_PATH.write_text("window.DEM_LAYERS = " + json.dumps(meta, separators=(",", ":"))
                         + ";\n", encoding="utf-8")
    print(f"  merged tile source into {META_PATH.name}", flush=True)


def main():
    if HS_PATH.exists():
        print(f"Stage 1 skipped — {HS_PATH.name} already exists", flush=True)
    else:
        print("Stage 1: 2 m hillshade GeoTIFF (full 84 GB pass — slow)...", flush=True)
        stage1_hillshade()
    print("Stage 2: cutting XYZ tiles z8-z16...", flush=True)
    bounds = stage2_tiles()
    merge_meta(bounds)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
