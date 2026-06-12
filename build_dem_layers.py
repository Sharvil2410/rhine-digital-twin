"""
Build draped DEM imagery layers for the dashboard from the PANGAEA DEM
(data_raw/rhine_dem.tif, 1 m, EPSG:25832, ~84 GB, striped, no overviews).

Outputs (one-off build — rerun only if the DEM changes):
    dashboard/data/dem/overview.png        whole corridor, 60 m
    dashboard/data/dem/<station>.png       5 x 5 km patch per gauge, 4 m
    dashboard/data/dem_layers.js           window.DEM_LAYERS = {...}

The GeoTIFF is striped in 1-px rows with no overviews, so the overview is
assembled from every 60th row (decimated reads) instead of one full-raster
read that would scan all ~90 GB. The CRS tag is a degenerate LOCAL_CS, so
EPSG:25832 is forced explicitly.
"""

import importlib.util
import json
import os
from pathlib import Path

# rasterio wheels on Windows don't register their bundled PROJ database; the
# env var must be set BEFORE rasterio's DLLs load, hence find_spec not import
_proj_data = Path(importlib.util.find_spec("rasterio").origin).parent / "proj_data"
if _proj_data.exists():
    os.environ.setdefault("PROJ_DATA", str(_proj_data))
    os.environ.setdefault("PROJ_LIB", str(_proj_data))

import numpy as np
import rasterio
from matplotlib import colormaps
from matplotlib.colors import LightSource, Normalize, to_hex
from matplotlib.image import imsave
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform as warp_transform
from rasterio.windows import Window
from rasterio.transform import Affine, array_bounds

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEM_PATH = PROJECT_ROOT / "data_raw" / "rhine_dem.tif"
OUT_DIR = Path(__file__).resolve().parent / "data" / "dem"
CRS_UTM = CRS.from_epsg(25832)   # file tag is LOCAL_CS — force the real CRS
CRS_WGS = CRS.from_epsg(4326)
NODATA = 0.0                     # fill value used by the PANGAEA mosaic
CMAP = colormaps["terrain"]

OVERVIEW_STEP = 60               # m -> px decimation for the reach overview
PATCH_HALF_M = 2500              # station patches are 5 x 5 km ...
PATCH_RES = 4                    # ... at 4 m resolution

STATIONS = {                     # matching the dashboard pins
    "kaub": (7.764962, 50.085438),
    "bonn": (7.108045, 50.736398),
    "duisburg_ruhrort": (6.727927, 51.455345),
    "wesel": (6.606820, 51.646143),
}


def read_overview(src):
    wo = src.width // OVERVIEW_STEP
    rows = range(0, src.height - OVERVIEW_STEP + 1, OVERVIEW_STEP)
    arr = np.empty((len(rows), wo), dtype=np.float32)
    for i, r in enumerate(rows):
        arr[i] = src.read(1, window=Window(0, r, src.width, 1),
                          out_shape=(1, wo), resampling=Resampling.average)[0]
        if i % max(1, len(rows) // 10) == 0:
            print(f"  overview rows {i}/{len(rows)}", flush=True)
    t = src.transform
    transform = Affine(OVERVIEW_STEP * t.a, 0, t.c, 0, OVERVIEW_STEP * t.e, t.f)
    return arr, transform


def read_patch(src, lon, lat):
    (x,), (y,) = warp_transform(CRS_WGS, CRS_UTM, [lon], [lat])
    t = src.transform
    col0 = int((x - PATCH_HALF_M - t.c) / t.a)
    row0 = int((t.f - (y + PATCH_HALF_M)) / -t.e)
    n_px = 2 * PATCH_HALF_M
    col0 = max(0, min(col0, src.width - n_px))
    row0 = max(0, min(row0, src.height - n_px))
    out = n_px // PATCH_RES
    arr = src.read(1, window=Window(col0, row0, n_px, n_px),
                   out_shape=(out, out), resampling=Resampling.average)
    transform = Affine(PATCH_RES * t.a, 0, t.c + col0 * t.a,
                       0, PATCH_RES * t.e, t.f + row0 * t.e)
    return arr.astype(np.float32), transform


def to_wgs84(arr, transform):
    bounds = array_bounds(arr.shape[0], arr.shape[1], transform)
    dst_t, dst_w, dst_h = calculate_default_transform(
        CRS_UTM, CRS_WGS, arr.shape[1], arr.shape[0], *bounds)
    dst = np.full((dst_h, dst_w), NODATA, dtype=np.float32)
    reproject(arr, dst, src_transform=transform, src_crs=CRS_UTM,
              dst_transform=dst_t, dst_crs=CRS_WGS,
              src_nodata=NODATA, dst_nodata=NODATA,
              resampling=Resampling.bilinear)
    w, s, e, n = array_bounds(dst_h, dst_w, dst_t)
    return dst, [round(w, 6), round(s, 6), round(e, 6), round(n, 6)]


def render_pngs(arr, vmin, vmax, res_m, stem):
    """Write both renderings of a layer: hillshade-blended color relief and a
    pure grayscale hillshade (for draping over satellite imagery)."""
    mask = ~np.isfinite(arr) | (arr <= NODATA)
    marr = np.ma.masked_array(arr, mask)
    alpha = np.where(mask, 0.0, 1.0)
    ls = LightSource(azdeg=315, altdeg=45)

    rgba = ls.shade(marr, cmap=CMAP, norm=Normalize(vmin, vmax),
                    blend_mode="soft", vert_exag=3, dx=res_m, dy=res_m)
    rgba[..., 3] = alpha
    p_color = OUT_DIR / f"{stem}_color.png"
    imsave(p_color, np.clip(rgba, 0, 1))

    hs = np.ma.filled(ls.hillshade(marr, vert_exag=4, dx=res_m, dy=res_m), 0.5)
    p_hs = OUT_DIR / f"{stem}_hs.png"
    imsave(p_hs, np.clip(np.dstack([hs, hs, hs, alpha]), 0, 1))

    print(f"  wrote {p_color.name} ({p_color.stat().st_size/1e6:.1f} MB) + "
          f"{p_hs.name} ({p_hs.stat().st_size/1e6:.1f} MB)  "
          f"{arr.shape[1]}x{arr.shape[0]}", flush=True)
    return {"color": f"data/dem/{p_color.name}", "hillshade": f"data/dem/{p_hs.name}"}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.png"):
        old.unlink()
    layers = []
    with rasterio.open(DEM_PATH) as src:
        print("Reading reach overview (every 60th row)...", flush=True)
        ov, ov_t = read_overview(src)
        valid = ov[np.isfinite(ov) & (ov > NODATA)]
        vmin, vmax = (round(float(v), 1) for v in np.percentile(valid, [2, 98]))
        print(f"  color range (p2-p98): {vmin} - {vmax} m", flush=True)

        ov_wgs, ov_bounds = to_wgs84(ov, ov_t)
        files = render_pngs(ov_wgs, vmin, vmax, OVERVIEW_STEP, "overview")
        layers.append({"name": "overview", "bounds": ov_bounds, **files})

        for key, (lon, lat) in STATIONS.items():
            print(f"Reading {key} patch (5x5 km @ {PATCH_RES} m)...", flush=True)
            patch, p_t = read_patch(src, lon, lat)
            p_wgs, p_bounds = to_wgs84(patch, p_t)
            files = render_pngs(p_wgs, vmin, vmax, PATCH_RES, key)
            layers.append({"name": key, "bounds": p_bounds, **files})

    stops = [[round(f, 3), to_hex(CMAP(f))] for f in np.linspace(0, 1, 9)]
    meta = {"vmin": vmin, "vmax": vmax, "stops": stops, "layers": layers,
            "source": "PANGAEA.919308 (incl. echo-sounded bathymetry)"}
    js = "window.DEM_LAYERS = " + json.dumps(meta, separators=(",", ":")) + ";\n"
    (OUT_DIR.parent / "dem_layers.js").write_text(js, encoding="utf-8")
    print(f"Wrote {OUT_DIR.parent / 'dem_layers.js'}", flush=True)


if __name__ == "__main__":
    main()
