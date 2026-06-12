"""
Whole-reach COLOR-RELIEF tile pyramid (companion to build_dem_tiles.py).

Stage 1 — stream data_raw/rhine_dem.tif once more and write a 2 m elevation
raster (uint16, centimetres, nodata 0) with overviews:
    data_processed/rhine_elev_2m.tif           (also usable in QGIS)

Stage 2 — cut web-mercator XYZ tiles z8..z15, each rendered like the existing
color drape (terrain colormap on the global thesis range + soft hillshade,
computed per tile with a pixel buffer so there are no seams):
    dashboard/data/dem_tiles_color/{z}/{x}/{y}.png

The tile source is merged into dashboard/data/dem_layers.js as `tiles_color`.
Run AFTER build_dem_tiles.py (reuses its vmin/vmax color range).
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
from matplotlib import colormaps
from matplotlib.colors import LightSource, Normalize
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEM_PATH = PROJECT_ROOT / "data_raw" / "rhine_dem.tif"
ELEV_PATH = PROJECT_ROOT / "data_processed" / "rhine_elev_2m.tif"
TILE_DIR = Path(__file__).resolve().parent / "data" / "dem_tiles_color"
META_PATH = Path(__file__).resolve().parent / "data" / "dem_layers.js"

CRS_UTM = CRS.from_epsg(25832)
CRS_WGS = CRS.from_epsg(4326)
CRS_MERC = CRS.from_epsg(3857)
NODATA_ELEV = 0.0
STEP = 2
VERT_EXAG = 3
MIN_Z, MAX_Z = 8, 15          # z15 ≈ 3 m/px ground — color stays crisp, half the tiles
CHUNK = 512
BUF = 4                       # px buffer per tile so hillshade has no seams
CMAP = colormaps["terrain"]


def stage1_elevation():
    t0 = time.time()
    with rasterio.open(DEM_PATH) as src:
        W, H = src.width, src.height
        ow, oh = W // STEP, H // STEP
        transform = Affine(STEP * src.transform.a, 0, src.transform.c,
                           0, STEP * src.transform.e, src.transform.f)
        profile = dict(driver="GTiff", width=ow, height=oh, count=1,
                       dtype="uint16", crs=CRS_UTM, transform=transform,
                       nodata=0, tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate", predictor=2, BIGTIFF="IF_SAFER")
        with rasterio.open(ELEV_PATH, "w", **profile) as dst:
            for cs in range(0, oh, CHUNK):
                ce = min(cs + CHUNK, oh)
                out = np.empty((ce - cs, ow), dtype=np.uint16)
                for i, out_r in enumerate(range(cs, ce)):
                    r = min(out_r * STEP, H - 1)
                    a = src.read(1, window=Window(0, r, W, 1))[0][:ow * STEP]
                    a = a.reshape(ow, STEP).astype(np.float32)
                    a[a <= NODATA_ELEV] = np.nan
                    with np.errstate(invalid="ignore"):
                        m = np.nanmean(a, axis=1)
                    out[i] = np.where(np.isnan(m), 0,
                                      np.clip(m * 100.0, 1, 65535)).astype(np.uint16)
                dst.write(out, 1, window=Window(0, cs, ow, ce - cs))
                if (cs // CHUNK) % 10 == 0:
                    print(f"  stage1 rows {ce}/{oh}  ({(time.time()-t0)/60:.1f} min)",
                          flush=True)
            print("  building overviews...", flush=True)
            dst.build_overviews([2, 4, 8, 16, 32, 64, 128, 256], Resampling.average)
    print(f"  stage1 done in {(time.time()-t0)/60:.1f} min -> {ELEV_PATH.name} "
          f"({ELEV_PATH.stat().st_size/1e9:.2f} GB)", flush=True)


WORLD = 2 * math.pi * 6378137 / 2


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


def merc_y_to_lat(y_m):
    return math.degrees(2 * math.atan(math.exp(y_m / 6378137)) - math.pi / 2)


def stage2_tiles(vmin, vmax):
    t0 = time.time()
    ls = LightSource(azdeg=315, altdeg=45)
    norm = Normalize(vmin, vmax)
    with rasterio.open(ELEV_PATH) as src:
        wgs = transform_bounds(CRS_UTM, CRS_WGS, *src.bounds)
        # WarpedVRT forbids boundless reads — give it a padded tile-aligned
        # extent so every (buffered) tile window falls inside (see
        # build_dem_tiles.py for details).
        grid_res = 2 * WORLD / 2 ** MAX_Z / 256
        gx0, gy0 = lonlat_to_tile(wgs[0], wgs[3], MIN_Z)
        gx1, gy1 = lonlat_to_tile(wgs[2], wgs[1], MIN_Z)
        nw = tile_merc_bounds(MIN_Z, gx0, gy0)
        se = tile_merc_bounds(MIN_Z, gx1, gy1)
        margin = 256 * grid_res
        vrt_transform = Affine(grid_res, 0, nw[0] - margin, 0, -grid_res, nw[3] + margin)
        vrt_w = round((se[2] - nw[0] + 2 * margin) / grid_res)
        vrt_h = round((nw[3] - se[1] + 2 * margin) / grid_res)
        vrt = WarpedVRT(src, crs=CRS_MERC, resampling=Resampling.bilinear,
                        src_nodata=0, nodata=0, transform=vrt_transform,
                        width=vrt_w, height=vrt_h)

        def render_tile(z, x, y):
            w, s, e, n = tile_merc_bounds(z, x, y)
            res = (e - w) / 256
            win = vrt.window(w - BUF * res, s - BUF * res, e + BUF * res, n + BUF * res)
            size = 256 + 2 * BUF
            raw = vrt.read(1, window=win, out_shape=(size, size))
            if not raw.any():
                return None
            elev = raw.astype(np.float32) / 100.0
            mask = raw == 0
            res_ground = res * math.cos(math.radians(merc_y_to_lat((s + n) / 2)))
            rgba = ls.shade(np.ma.masked_array(elev, mask), cmap=CMAP, norm=norm,
                            blend_mode="soft", vert_exag=VERT_EXAG,
                            dx=res_ground, dy=res_ground)
            rgba = (np.clip(rgba[BUF:-BUF, BUF:-BUF], 0, 1) * 255).astype(np.uint8)
            rgba[..., 3] = np.where(mask[BUF:-BUF, BUF:-BUF], 0, 255)
            return rgba if rgba[..., 3].any() else None

        prev_set = None
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
                rgba = render_tile(z, x, y)
                if rgba is None:
                    continue
                cur.add((x, y))
                d = TILE_DIR / str(z) / str(x)
                d.mkdir(parents=True, exist_ok=True)
                Image.fromarray(rgba, "RGBA").save(d / f"{y}.png")
            prev_set = cur
            print(f"  z{z}: {len(cur)} tiles ({len(candidates)} candidates, "
                  f"{(time.time()-t0)/60:.1f} min)", flush=True)

    size_mb = sum(f.stat().st_size for f in TILE_DIR.rglob("*.png")) / 1e6
    print(f"  stage2 done: {size_mb:.0f} MB", flush=True)
    return [round(v, 6) for v in wgs]


def main():
    txt = META_PATH.read_text(encoding="utf-8")
    meta = json.loads(txt[txt.index("=") + 1:].rstrip().rstrip(";"))
    vmin, vmax = meta["vmin"], meta["vmax"]
    print(f"Color range from dem_layers.js: {vmin}-{vmax} m", flush=True)

    if ELEV_PATH.exists():
        print(f"Stage 1 skipped — {ELEV_PATH.name} already exists", flush=True)
    else:
        print("Stage 1: 2 m elevation GeoTIFF (full 84 GB pass — slow)...", flush=True)
        stage1_elevation()
    print("Stage 2: cutting color XYZ tiles z8-z15...", flush=True)
    bounds = stage2_tiles(vmin, vmax)

    meta["tiles_color"] = {"url": "data/dem_tiles_color/{z}/{x}/{y}.png",
                           "minZoom": MIN_Z, "maxZoom": MAX_Z, "bounds": bounds}
    META_PATH.write_text("window.DEM_LAYERS = " + json.dumps(meta, separators=(",", ":"))
                         + ";\n", encoding="utf-8")
    print(f"  merged tiles_color into {META_PATH.name}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
