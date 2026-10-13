#!/usr/bin/env sh
# Launch the reimplementation. Simulator mode by default (no hardware needed).
# Point PY at any Python >=3.10 if `python3` isn't on PATH.
PY="${PY:-python3}"
exec "$PY" -m pyeaton.app "$@"
