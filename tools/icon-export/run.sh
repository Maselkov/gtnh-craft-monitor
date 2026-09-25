#!/usr/bin/env bash
# Build a game-data bundle (item icons, icon lookup, item catalog) from a
# GTNH client pack, headlessly, in Docker.
#
#   tools/icon-export/run.sh <GT_New_Horizons_..._Java_17-25.zip>
#       [--faithful GTNH-Faithful-x32.vX.Y.Z.zip] [--install [--data-dir DIR]]
#
# The pack is the MultiMC/Prism client zip from
# https://downloads.gtnewhorizons.com/Multi_mc_downloads/ (not the server
# pack). Output goes to tools/icon-export/out/. --faithful also renders the
# icons with the GTNH Faithful x32 resource pack (a release zip from
# https://github.com/Ethryan/GTNH-Faithful-Textures), into
# images-faithful32.zip; that takes a second launch. --install also puts the
# bundle in the server's data directory (default server/data; for the
# prebuilt-image setup, the directory mounted at /app/data) and selects it,
# as if it had been picked on the Game data page. CI publishes bundles for
# every GTNH release, so this is for packs it doesn't cover.
#
# Environment: GCM_VERSION_LABEL (default: from the pack's file name),
# GCM_MAX_MEMORY (default 6G), GCM_ICON_SIZE (default 64),
# GCM_ANIMATION_TICKS (default 160: 8 s of animation; 0 = still icons), GCM_TIMEOUT
# (seconds, default 3600), GCM_CACHE_DIR (default ~/.cache/gcm-icon-export -
# libraries and Minecraft assets, ~250 MB).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

usage() {
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

pack=""
faithful=""
install=false
data_dir="$repo/server/data"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) install=true ;;
        --faithful) faithful="${2:?--faithful needs a resource pack zip}"; shift ;;
        --data-dir) data_dir="${2:?--data-dir needs a directory}"; shift ;;
        -*) usage ;;
        *) pack="$1" ;;
    esac
    shift
done
[[ -n "$pack" && -f "$pack" ]] || usage
pack="$(realpath "$pack")"
faithful_args=()
if [[ -n "$faithful" ]]; then
    [[ -f "$faithful" ]] || { echo "No such file: $faithful" >&2; exit 2; }
    faithful="$(realpath "$faithful")"
    # Mounted under its own name: that's the name the game lists it by, and
    # its release version is read from it.
    faithful_args=(-v "$faithful:/faithful/$(basename "$faithful"):ro"
        -e GCM_FAITHFUL_PACK="/faithful/$(basename "$faithful")")
fi

# GT_New_Horizons_2.9.0-beta-3_Java_17-26.zip -> 2.9.0-beta-3
label="${GCM_VERSION_LABEL:-}"
if [[ -z "$label" ]]; then
    label="$(basename "$pack" .zip)"
    label="${label#GT_New_Horizons_}"
    label="${label%%_Java*}"
fi
if ! [[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    echo "Can't use '$label' as a version name; set GCM_VERSION_LABEL." >&2
    exit 2
fi

out="$here/out"
cache="${GCM_CACHE_DIR:-$HOME/.cache/gcm-icon-export}"
mkdir -p "$out" "$cache"

docker build -t gcm-icon-export "$here"

# Run as the calling user so the output and cache aren't root-owned.
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -e GCM_VERSION_LABEL="$label" \
    -e GCM_MAX_MEMORY -e GCM_ICON_SIZE -e GCM_TIMEOUT -e GCM_FAITHFUL_VERSION \
    -e GCM_ANIMATION_TICKS \
    --shm-size=2g \
    -v "$pack:/pack/pack.zip:ro" \
    "${faithful_args[@]}" \
    -v "$out:/out" \
    -v "$cache:/cache" \
    gcm-icon-export

if $install; then
    gamedata="$data_dir/gamedata"
    dest="$gamedata/$label"
    mkdir -p "$dest"
    rm -f "$dest"/images*.zip
    cp "$out/data.json" "$out"/images*.zip "$out/icons_lookup.json" \
        "$out/item_catalog.txt" "$dest/"
    # Same format gcm/gamedata.py writes; "previous" keeps the last
    # version around for switching back. Default textures; with --faithful,
    # the Game data page switches to Faithful without downloading anything.
    previous="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' \
        "$gamedata/selected.json" 2>/dev/null || true)"
    if [[ -n "$previous" && "$previous" != "$label" ]]; then
        previous="\"$previous\""
    else
        previous=null
    fi
    printf '{"version": "%s", "textures": "default", "previous": %s}\n' \
        "$label" "$previous" > "$gamedata/selected.json"
    echo "Installed GTNH $label game data into $dest."
    echo "Restart the server to use it (or pick it on the Game data page)."
fi
