#!/bin/sh
set -e

# Läuft als Root (Bildstart) → Volume-Rechte sicherstellen, dann zu appuser wechseln
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R appuser:appuser /data
    exec gosu appuser "$@"
fi

exec "$@"
