#!/usr/bin/env bash
# Nodo opcional en Chile (PC viejo con Linux, o Android con Termux).
# Corre el monitor SOLO para tiendas que bloquean a GitHub (Unimarc) y sube lo leído a la
# rama `node-data`. La nube lo integra en cada ventana.
#
# Requisitos (una vez):
#   git clone https://github.com/DiCloudd/radar-super-cl && cd radar-super-cl
#   python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
#   git config --global credential.helper store
#   Token "fine-grained" de GitHub con Contents: Read and write SOLO para este repo; se pide
#   una vez al primer push (usuario = tu usuario de GitHub, contraseña = el token).
#   Nunca lo pegues en un chat ni en un archivo del repo.
set -u
cd "$(dirname "$0")/.."
. .venv/bin/activate
export MONITOR_RUNNER=chile
export RADAR_DB="$PWD/data/chile.db"
STORES="${NODE_STORES:-unimarc,alvi}"
ORIGIN="$(git remote get-url origin)"
OUT="${TMPDIR:-/tmp}/radar-node"
mkdir -p data "$OUT"

while true; do
  git pull -q --ff-only origin main || true
  python -m radar.main window --minutes 12 --only "$STORES"
  python -m radar.main node-export "$OUT/node.json.gz" --stores "$STORES"
  (
    rm -rf "$OUT/repo" && mkdir -p "$OUT/repo" && cd "$OUT/repo" &&
    git init -q -b node-data &&
    cp ../node.json.gz node.json.gz && git add node.json.gz &&
    git -c user.name="radar-node" -c user.email="radar-node@users.noreply.github.com" \
        commit -q -m "nodo chile $(date -u +%FT%TZ)" &&
    git push -q --force "$ORIGIN" node-data
  ) || echo "⚠️ no se pudo subir (sin red o sin token); se reintenta en la próxima vuelta"
  sleep 30
done
