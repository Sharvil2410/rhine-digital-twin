# Rhine Digital Twin — SWOT × In-situ dashboard

Interactive 3D dashboard for the MSc thesis *"SWOT-derived water level and discharge on the
Rhine"*. A CesiumJS satellite globe shows the four study gauges (Kaub, Bonn, Duisburg-Ruhrort,
Wesel); clicking a gauge opens a panel with:

- **Water level** — in-situ PegelOnline stage vs DAHITI satellite altimetry
  (dual-axis or demeaned anomaly overlay, with DAHITI uncertainty bars), plus the
  thesis validation metrics.
- **Discharge** — in-situ PegelOnline vs SWOT **modified Manning** estimates
  (switchable: fixed 3.0×10⁻⁴ vs SWOT-observed slope, literature n=0.030 vs calibrated n),
  with GloFAS and GEOGloWS toggleable in the legend and matched-pass skill metrics
  (n, r, RMSE, bias, NSE, KGE) computed in the browser.
- **Live layer** — the current measurement and the last 30 days of stage and discharge are
  fetched from the PegelOnline REST API on every page load, so the twin always shows the
  river *now*.
- **Map layers** — the **thalweg** (blue, channel-centre line from
  `outputs/longitudinal_profile/rhine_channel_centre_line.geojson`) and the
  **SWOT-followed river centreline** (red, from `data_raw/River_Centreline.gpkg`)
  are drawn over the imagery, toggleable via the header chips.
- **Longitudinal profile** — header chip "📈 Long profile": DEM thalweg bed and
  DEM-along-centerline elevation vs chainage (Bingen → Rees), overlaid with SWOT
  water-surface profiles from a high-flow (2024-06-06/10) and a low-flow (2023-10-21/24)
  event. Segment buttons (whole reach, Kaub→Bonn, Bonn→Duisburg-Ruhrort,
  Duisburg-Ruhrort→Wesel) zoom both the chart **and** the 3D camera to that stretch;
  dashed verticals mark the gauges at their corrected chainages.

## Run locally

```
cd "E:\MSC RESEARCH\VS CODE\MSc_SWOT_Rhine"
python -m http.server 8765 --directory dashboard
```

then open <http://localhost:8765>. (Double-clicking `index.html` mostly works too, but the
live PegelOnline fetch is more reliable when served over http.)

Deep links work: `index.html#kaub`, `#bonn`, `#duisburg_ruhrort`, `#wesel`, `#profile`.

- **DEM drape** — the PANGAEA DEM (`data_raw/rhine_dem.tif`, includes the echo-sounded
  channel bathymetry) is draped over the imagery. The control card (bottom-left) switches
  between **hillshade** (default) and **color relief** rendering and has an **intensity
  slider** that blends the drape with the basemap. Toggle the whole layer with the
  "DEM (PANGAEA)" chip.

  Hillshade mode streams a **whole-reach XYZ tile pyramid** (z8–z16, ≈1.5 m/px ground
  resolution at z16 — QGIS-level detail along all 320 km), built once by:

  ```
  python dashboard/build_dem_tiles.py
  ```

  (≈30 min: stage 1 streams the 84 GB GeoTIFF once into
  `data_processed/rhine_hillshade_2m.tif` — also loadable in QGIS — and stage 2 cuts
  `dashboard/data/dem_tiles/{z}/{x}/{y}.png`, skipping empty tiles.)

  Color-relief mode uses single draped images (60 m reach overview + 4 m station
  patches) built by:

  ```
  python dashboard/build_dem_layers.py
  ```

## Update the data

The charts read `data/stations_data.js`, generated from the processed thesis CSVs
(15-min series are downsampled to daily means). Whenever files in `data_processed/`
change, regenerate with:

```
python dashboard/build_dashboard_data.py
```

## Publish for everyone (free)

The dashboard is a fully static site (`index.html` + `data/`), so any static host works:

1. Create a GitHub repository, copy the `dashboard/` folder contents into it.
2. Settings → Pages → deploy from branch → root.
3. Share `https://<user>.github.io/<repo>/`.

PegelOnline's API sends `Access-Control-Allow-Origin: *`, so the live layer keeps working
from any host.

## Optional: Google photorealistic 3D

By default the globe uses free Esri World Imagery (no API key needed). For the
"Google Earth" look, get a Google Maps Platform key with the *Map Tiles API* enabled and
paste it into `GOOGLE_MAPS_API_KEY` at the top of the `<script>` block in `index.html`.
The scene then switches to Google Photorealistic 3D Tiles automatically.

## Data credits

SWOT (NASA/CNES) · DAHITI (DGFI-TUM) · PegelOnline (WSV) · GloFAS (Copernicus) · GEOGloWS ·
bathymetry-derived cross-sections from PANGAEA.919308 · imagery Esri/Maxar.
