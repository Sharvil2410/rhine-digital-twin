# Reproducible environment for the dashboard build scripts (build_*.py).
#
# The dashboard itself is static and needs no container to run. This image
# exists so the data bundle and DEM layers can be regenerated on any machine
# with the same Python and library versions.
#
# The build scripts locate the thesis data relative to the *project root*
# (the parent of this folder): data_processed/, outputs/, data_raw/. Mount
# that root at /work and run from it:
#
#   docker build -t rhine-twin-builder ./dashboard
#   docker run --rm -v "$PWD:/work" rhine-twin-builder
#   docker run --rm -v "$PWD:/work" rhine-twin-builder python dashboard/build_borders.py
#
# Rasterio wheels bundle GDAL, so no system GDAL packages are needed.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /work

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

# Default: rebuild the station data bundle. Override the command for the
# DEM or border builders (see the header of this file).
CMD ["python", "dashboard/build_dashboard_data.py"]
