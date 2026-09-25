#!/bin/sh
# ORBIS - dois cliques para subir a interface e o motor no macOS.
cd "$(dirname "$0")" || exit 1
exec python3 start.py "$@"
