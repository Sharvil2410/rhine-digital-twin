"""
Build the static data bundle for the Rhine SWOT digital-twin dashboard.

Reads the processed thesis CSVs in data_processed/ and writes
dashboard/data/stations_data.js  (window.DASHBOARD_DATA = {...})

The 15-min PegelOnline series are downsampled to daily means so the
whole bundle stays small enough for a static website. Run again any
time the processed CSVs change:

    python dashboard/build_dashboard_data.py
"""

import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = PROJECT_ROOT / "data_processed"
OUTPUTS = PROJECT_ROOT / "outputs"
OUT_FILE = Path(__file__).resolve().parent / "data" / "stations_data.js"

# PegelOnline UUIDs / coordinates fetched from
# https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations.json?waters=RHEIN
STATIONS = [
    {
        "key": "kaub",
        "name": "Kaub",
        "pegelonline_uuid": "1d26e504-7f9e-480a-b52c-5932be6549ab",
        "pegelonline_shortname": "KAUB",
        "lat": 50.085438,
        "lon": 7.764962,
        "rhine_km": 546.23,
        "metrics_dir": "Kaub",
    },
    {
        "key": "bonn",
        "name": "Bonn",
        "pegelonline_uuid": "593647aa-9fea-43ec-a7d6-6476a76ae868",
        "pegelonline_shortname": "BONN",
        "lat": 50.736398,
        "lon": 7.108045,
        "rhine_km": 654.8,
        "metrics_dir": "Bonn",
    },
    {
        "key": "duisburg_ruhrort",
        "name": "Duisburg-Ruhrort",
        "pegelonline_uuid": "c0f51e35-d0e8-4318-afaf-c5fcbc29f4c1",
        "pegelonline_shortname": "DUISBURG-RUHRORT",
        "lat": 51.455345,
        "lon": 6.727927,
        "rhine_km": 780.8,
        "metrics_dir": "Duisburg_Ruhrort",
    },
    {
        "key": "wesel",
        "name": "Wesel",
        "pegelonline_uuid": "f33c3cc9-dc4b-4b77-baa9-5a5f10704398",
        "pegelonline_shortname": "WESEL",
        "lat": 51.646143,
        "lon": 6.606820,
        "rhine_km": 814.0,
        "metrics_dir": "Wesel",
    },
]


def daily_series(csv_path, value_col, round_to=2):
    """15-min (or daily) CSV -> [[YYYY-MM-DD, value], ...] daily means."""
    if not csv_path.exists():
        print(f"  !! missing {csv_path.name}")
        return []
    df = pd.read_csv(csv_path, usecols=["date", value_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna()
    daily = df.groupby("date")[value_col].mean().round(round_to)
    return [[d, float(v)] for d, v in daily.items()]


def dahiti_series(csv_path):
    if not csv_path.exists():
        print(f"  !! missing {csv_path.name}")
        return []
    df = pd.read_csv(csv_path)
    df["water_level_m"] = pd.to_numeric(df["water_level_m"], errors="coerce")
    df["water_level_uncertainty_m"] = pd.to_numeric(
        df.get("water_level_uncertainty_m"), errors="coerce"
    )
    df = df.dropna(subset=["datetime", "water_level_m"])
    return [
        [str(r["datetime"]), round(float(r["water_level_m"]), 3),
         None if pd.isna(r["water_level_uncertainty_m"])
         else round(float(r["water_level_uncertainty_m"]), 3)]
        for _, r in df.iterrows()
    ]


def swot_manning(csv_path):
    """Calibrated modified-Manning CSV -> one record per SWOT pass.

    Stations with several cross-section profiles are averaged per pass
    datetime so the dashboard shows one point per overpass.
    """
    if not csv_path.exists():
        print(f"  !! missing {csv_path.name}")
        return []
    df = pd.read_csv(csv_path)
    cols = {
        "wse_m": "wse",
        "W_t_m": "width",
        "Q_manning_fixed_slope_m3s": "q_fixed",
        "Q_manning_swot_slope_m3s": "q_swot",
        "Q_manning_fixed_slope_calibrated_m3s": "q_fixed_cal",
        "Q_manning_swot_slope_calibrated_m3s": "q_swot_cal",
    }
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    grouped = df.groupby("datetime")[list(cols)].mean().reset_index()
    out = []
    for _, r in grouped.iterrows():
        rec = {"t": str(r["datetime"])}
        for src, dst in cols.items():
            rec[dst] = None if pd.isna(r[src]) else round(float(r[src]), 1 if "Q_" in src else 3)
        out.append(rec)
    out.sort(key=lambda r: r["t"])
    return out


def dahiti_metrics(station):
    f = OUTPUTS / station["metrics_dir"] / f"{station['key']}_dahiti_insitu_agreement_metrics.csv"
    if not f.exists():
        return None
    row = pd.read_csv(f).iloc[0]
    return {
        "n": int(row["n_matched"]),
        "r": round(float(row["pearson_r"]), 4),
        "rmse_m": round(float(row["rmse_demeaned_m"]), 3),
        "bias_m": round(float(row["mean_bias_demeaned_m"]), 3),
        "datum_offset_m": round(float(row["datum_offset_m"]), 3),
    }


def haversine_km(a, b):
    from math import asin, cos, radians, sin, sqrt
    lon1, lat1, lon2, lat2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def gpkg_line_parts(path, table="River_Centreline", col="geom", max_total_pts=4000):
    """Read (Multi)LineString parts from a GeoPackage with stdlib only.

    The GPKG is already EPSG:4326 so no reprojection is needed; geometry blobs
    are a GeoPackage binary header followed by ISO WKB.
    """
    import sqlite3
    import struct

    def parse(buf, off, parts):
        bo = "<" if buf[off] == 1 else ">"
        typ = struct.unpack_from(bo + "I", buf, off + 1)[0] % 1000
        if typ == 2:                                   # LineString
            n = struct.unpack_from(bo + "I", buf, off + 5)[0]
            xy = struct.unpack_from(bo + f"{2 * n}d", buf, off + 9)
            parts.append([[xy[2 * i], xy[2 * i + 1]] for i in range(n)])
            return off + 9 + 16 * n
        if typ == 5:                                   # MultiLineString
            n = struct.unpack_from(bo + "I", buf, off + 5)[0]
            off += 9
            for _ in range(n):
                off = parse(buf, off, parts)
            return off
        raise ValueError(f"unsupported WKB type {typ}")

    parts = []
    con = sqlite3.connect(str(path))
    for (blob,) in con.execute(f'SELECT "{col}" FROM "{table}"'):
        if blob is None:
            continue
        env = (blob[3] >> 1) & 0x07                    # GPB flags -> envelope size
        parse(blob, 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env], parts)
    con.close()

    total = sum(len(p) for p in parts)
    step = max(1, total // max_total_pts)
    thinned = []
    for p in parts:
        q = p[::step]
        if q[-1] != p[-1]:
            q.append(p[-1])
        if len(q) >= 2:
            thinned.append([[round(x, 5), round(y, 5)] for x, y in q])
    print(f"  GPKG centreline: {len(parts)} part(s), {total} pts -> "
          f"{sum(len(p) for p in thinned)} pts")
    return thinned


def build_profile():
    """Longitudinal-profile bundle: thalweg / SWOT centerline geometry for the
    3D map plus elevation profiles (DEM thalweg, DEM along centerline, SWOT WSE
    high- and low-flow passes) against chainage km (0 = Bingen)."""
    prof = OUTPUTS / "longitudinal_profile"
    out = {"label": "Bingen → Rees"}

    tp = pd.read_csv(prof / "rhine_thalweg_profile.csv").dropna(subset=["thalweg_elev_m"])
    out["thalweg"] = [[round(float(k), 2), round(float(e), 2)]
                      for k, e in zip(tp["distance_km"], tp["thalweg_elev_m"])]
    out["extent_km"] = [0.0, round(float(tp["distance_km"].max()), 1)]

    cs = pd.read_csv(prof / "swot_centreline_profile_samples.csv")
    cse = cs.dropna(subset=["elevation_m"])
    out["centerline_dem"] = [[round(float(k), 2), round(float(e), 2)]
                             for k, e in zip(cse["distance_km"], cse["elevation_m"])]

    hl = pd.read_csv(prof / "rhine_swot_centreline_vs_swot_wse_high_low_nodes.csv")
    for ev in ("high", "low"):
        d = hl[hl["event"] == ev].sort_values("distance_km")
        out[f"swot_{ev}"] = {
            "dates": sorted(d["swot_pass_date"].unique().tolist()),
            "pts": [[round(float(r.distance_km), 2), round(float(r.wse), 2),
                     str(r.swot_pass_date)] for r in d.itertuples()],
        }

    # Map geometries. Thalweg (blue) = channel-centre line; SWOT centreline
    # (red) = SWOT-followed river centreline from data_raw/River_Centreline.gpkg.
    cc = json.loads((prof / "rhine_channel_centre_line.geojson").read_text(encoding="utf-8"))
    coords = cc["features"][0]["geometry"]["coordinates"][::10]   # ~100 m spacing
    pts, km, prev = [], 0.0, None
    for lon, lat in coords:
        if prev is not None:
            km += haversine_km(prev, (lon, lat))
        prev = (lon, lat)
        pts.append([round(lon, 5), round(lat, 5), round(km, 2)])
    out["thalweg_geom"] = pts

    out["centerline_parts"] = gpkg_line_parts(
        PROJECT_ROOT / "data_raw" / "River_Centreline.gpkg", max_total_pts=4000)

    chain = pd.read_csv(PROJECT_ROOT / "scripts" / "Longitudinal_Profile_Scripts"
                        / "stations_corrected_chainage.csv")
    name2key = {"Kaub": "kaub", "Bonn": "bonn",
                "Duisburg-Ruhrort": "duisburg_ruhrort", "Wesel": "wesel"}
    out["stations_km"] = {name2key[r.station_name]: float(r.chainage_km)
                          for r in chain.itertuples() if r.station_name in name2key}

    print(f"Profile: thalweg {len(out['thalweg'])} pts, centerline {len(out['centerline_dem'])} pts, "
          f"SWOT high {len(out['swot_high']['pts'])} / low {len(out['swot_low']['pts'])} nodes")
    return out


def main():
    bundle = {"generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
              "stations": [], "profile": build_profile()}

    for st in STATIONS:
        key = st["key"]
        print(f"Station {st['name']}")
        rec = {k: st[k] for k in ("key", "name", "pegelonline_uuid",
                                  "pegelonline_shortname", "lat", "lon", "rhine_km")}

        rec["insitu_wl_daily"] = daily_series(
            DATA / f"{key}_pegelonline_waterlevel_2023_2026.csv", "water_level_m", 3)
        rec["insitu_q_daily"] = daily_series(
            DATA / f"{key}_pegelonline_discharge_2023_2026.csv", "discharge_m3s", 1)
        rec["dahiti_wl"] = dahiti_series(DATA / f"{key}_dahiti_waterlevel_2023_2026.csv")
        rec["swot_manning"] = swot_manning(
            DATA / f"{key}_swot_modified_manning_discharge_calibrated_2023_2026.csv")
        rec["glofas_q_daily"] = daily_series(
            DATA / f"{key}_glofas_discharge_2023_2026.csv", "discharge_m3s", 1)
        rec["geoglows_q_daily"] = daily_series(
            DATA / f"{key}_geoglows_discharge_2023_2026.csv", "discharge_m3s", 1)
        rec["dahiti_metrics"] = dahiti_metrics(st)

        print(f"  in-situ WL days: {len(rec['insitu_wl_daily'])}, "
              f"Q days: {len(rec['insitu_q_daily'])}, "
              f"DAHITI pts: {len(rec['dahiti_wl'])}, "
              f"SWOT passes: {len(rec['swot_manning'])}")
        bundle["stations"].append(rec)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    js = "window.DASHBOARD_DATA = " + json.dumps(bundle, separators=(",", ":")) + ";\n"
    OUT_FILE.write_text(js, encoding="utf-8")
    print(f"\nWrote {OUT_FILE}  ({OUT_FILE.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
