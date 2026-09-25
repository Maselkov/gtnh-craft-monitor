#!/usr/bin/env python3
"""
Container entrypoint for the headless icon export (see Dockerfile / run.sh).

Takes a GTNH MultiMC/Prism client pack zip, launches the game under Xvfb
with the gcmiconexport mod added, and collects what the mod writes:

    images.zip          item icons
    icons_lookup.json   item/fluid keys -> paths in images.zip
    item_catalog.txt    item IDs for oc/network_browser.lua
    export-report.json  counts and anything that failed to render
    data.json           the bundle manifest: GTNH version, counts, the
                        texture sets, and each file's size and SHA-256

Animated icons (GT materials, lava, ...) are captured frame by frame and
written into images.zip as animated PNGs (APNG) at the same paths. Frame 0
is the still icon, which is what anything that can't animate shows.

With --faithful, the game is launched a second time with that resource
pack enabled, and its renders go in images-faithful32.zip (same paths, so
the one lookup serves both). Icons that pass couldn't render fall back to
the default render.

Together they're a game-data bundle for one GTNH version, which the
server installs into DATA_DIR/gamedata/<version>/ (gcm/gamedata.py).

No launcher or Minecraft account is involved: the pack's own MultiMC patch
files (patches/*.json) list every library with its download URL, and the
game is started offline, the way a dev environment starts it. Downloads
(libraries, the vanilla client jar, assets) are cached under --cache.

The launch approach - RetroFuturaBootstrap main class, offline username,
Xvfb + Mesa software rendering - follows gtnh-factory-flow's dataset
pipeline (github.com/jackwrichards/gtnh-factory-flow, MIT).
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

MOD_JAR = Path(os.environ.get("GCM_MOD_JAR", "/opt/gcm/gcmiconexport.jar"))
# What the mod writes; export.py adds data.json once they're all there.
GAME_OUTPUTS = (
    "images.zip", "icons_lookup.json", "item_catalog.txt",
    "export-report.json",
)
# icons_lookup.json's key -> image path tables. Its "bleed" table maps the
# paths of icons drawn past the item box to how far, in 1/16ths of it.
LOOKUP_TABLES = ("by_key", "fluids_by_key", "by_label")

# Loading errors put FML's error screen up and wait for a click that never
# comes, so these have to be caught from the log rather than the exit code.
# Texture sets besides the default: what the server shows admins and the
# credit that goes with the renders (in data.json, the release notes and
# the zip itself).
FAITHFUL = {
    "file": "images-faithful32.zip",
    "name": "Faithful 32x",
    "credit": "GTNH Faithful x32 textures by Ethryan and contributors",
    "url": "https://github.com/Ethryan/GTNH-Faithful-Textures",
}

FATAL_LOG = re.compile(
    r"Fatal errors were detected during the transition"
    r"|---- Minecraft Crash Report ----"
    r"|Caught exception from [A-Za-z0-9_.:-]+"
)


def log(msg):
    print(f"[export] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------

def extract_pack(pack_zip, work_dir):
    target = work_dir / "pack"
    if target.exists():
        shutil.rmtree(target)
    log(f"Extracting {pack_zip.name}...")
    with zipfile.ZipFile(pack_zip) as zf:
        zf.extractall(target)
    roots = sorted(p.parent for p in target.glob("**/mmc-pack.json")
                   if len(p.relative_to(target).parts) <= 3)
    if not roots:
        sys.exit(f"No mmc-pack.json in {pack_zip} - this needs the "
                 "MultiMC/Prism client pack, not the server pack.")
    root = roots[0]
    for name in (".minecraft", "minecraft"):
        if (root / name / "mods").is_dir():
            return root, root / name
    sys.exit(f"No .minecraft/mods directory under {root}.")


def load_patches(pack_root):
    """The pack's components, in the order MultiMC applies them."""
    mmc_pack = json.loads((pack_root / "mmc-pack.json").read_text())
    components = mmc_pack["components"]
    patches = []
    for component in components:
        path = pack_root / "patches" / f"{component['uid']}.json"
        if not path.exists():
            sys.exit(f"Component {component['uid']} has no patch file "
                     f"({path}); this pack relies on launcher metadata "
                     "that isn't bundled.")
        patches.append(json.loads(path.read_text()))
    return sorted(patches, key=lambda p: p.get("order", 0))


# ---------------------------------------------------------------------------
# Libraries / assets
# ---------------------------------------------------------------------------

def rules_allow(rules):
    if not rules:
        return True
    allowed = False
    for rule in rules:
        os_name = rule.get("os", {}).get("name")
        if os_name is None or os_name == "linux":
            allowed = rule["action"] == "allow"
    return allowed


def maven_path(name):
    """group:artifact:version[:classifier][@ext] -> repository path."""
    coords, _, ext = name.partition("@")
    parts = coords.split(":")
    group, artifact, version = parts[:3]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    filename = f"{artifact}-{version}{classifier}.{ext or 'jar'}"
    return Path(*group.split("."), artifact, version, filename)


def library_key(name):
    """Identity for overriding: group:artifact[:classifier], no version."""
    parts = name.partition("@")[0].split(":")
    return ":".join(parts[:2] + parts[3:4])


def file_hash(path, algorithm="sha1"):
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, sha1=None):
    if dest.exists() and (sha1 is None or file_hash(dest) == sha1):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    # Forge's maven rejects urllib's default User-Agent with a 403.
    request = urllib.request.Request(
        url, headers={"User-Agent": "gcm-icon-export"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp, \
                    open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            if sha1 and file_hash(tmp) != sha1:
                raise IOError(f"sha1 mismatch for {url}")
            tmp.replace(dest)
            return dest
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Download failed: {url}: {e}") from e
            time.sleep(2 * (attempt + 1))


def resolve_libraries(patches, pack_root, cache):
    """Classpath jars and legacy natives jars. Later patches override
    earlier ones' libraries, as in MultiMC."""
    chosen = {}
    for patch in patches:
        for lib in patch.get("libraries", []) + patch.get("+libraries", []):
            if rules_allow(lib.get("rules")):
                chosen[library_key(lib["name"])] = lib

    jobs, classpath, natives = [], [], []
    for lib in chosen.values():
        name = lib["name"]
        if lib.get("MMC-hint") == "local":
            local = pack_root / "libraries" / maven_path(name).name
            if not local.exists():
                sys.exit(f"Local library {name} missing: expected {local}")
            classpath.append(local)
            continue
        if "natives" in lib:
            classifier = lib["natives"].get("linux")
            classifiers = lib.get("downloads", {}).get("classifiers", {})
            info = classifiers.get(classifier or "")
            if info:
                dest = (cache / "libraries"
                        / maven_path(f"{name}:{classifier}"))
                jobs.append((info["url"], dest, info.get("sha1")))
                natives.append(dest)
            # A natives-only entry has no main artifact of its own.
            if "artifact" not in lib.get("downloads", {}):
                continue
        artifact = lib.get("downloads", {}).get("artifact")
        if not artifact:
            sys.exit(f"Library {name} has no download URL.")
        dest = cache / "libraries" / maven_path(name)
        jobs.append((artifact["url"], dest, artifact.get("sha1")))
        classpath.append(dest)

    log(f"Resolving {len(jobs)} libraries...")
    run_downloads(jobs)
    return classpath, natives


def fetch_assets(asset_index, cache):
    assets = cache / "assets"
    index_path = download(
        asset_index["url"],
        assets / "indexes" / f"{asset_index['id']}.json",
        asset_index.get("sha1"))
    index = json.loads(index_path.read_text())
    jobs = []
    for obj in index["objects"].values():
        h = obj["hash"]
        url = f"https://resources.download.minecraft.net/{h[:2]}/{h}"
        jobs.append((url, assets / "objects" / h[:2] / h, h))
    log(f"Resolving {len(jobs)} assets...")
    run_downloads(jobs)
    return assets


def run_downloads(jobs):
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(download, *job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def extract_natives(jars, natives_dir):
    natives_dir.mkdir(parents=True, exist_ok=True)
    for jar in jars:
        with zipfile.ZipFile(jar) as zf:
            for member in zf.namelist():
                if not (member.startswith("META-INF/")
                        or member.endswith("/")):
                    zf.extract(member, natives_dir)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def disable_angelica(game_dir):
    """Run without Angelica, GTNH's rendering overhaul. It emulates GL's
    attribute stack, and item renderers that throw at the main menu (no
    player to read) leak entries on it that can't be unwound from outside,
    until it overflows and thousands of unrelated items fail to render. It
    adds nothing to still icons, and nothing in the pack requires it."""
    for jar in (game_dir / "mods").glob("angelica-*.jar"):
        log(f"Disabling {jar.name} for the export.")
        jar.rename(game_dir / (jar.name + ".disabled"))


def enable_resource_pack(game_dir, pack_zip):
    """Copies a resource pack into the game and turns it on, as picking it
    in the Resource Packs screen would. The GTNH pack ships no
    options.txt, so the game fills in every other option with defaults."""
    packs = game_dir / "resourcepacks"
    packs.mkdir(exist_ok=True)
    shutil.copy2(pack_zip, packs / pack_zip.name)
    options = game_dir / "options.txt"
    lines = []
    if options.exists():
        lines = [line for line in options.read_text().splitlines()
                 if not line.startswith("resourcePacks:")]
    lines.append("resourcePacks:" + json.dumps([pack_zip.name]))
    options.write_text("\n".join(lines) + "\n")


def build_command(patches, pack_root, game_dir, cache, args):
    main_class, main_jar, asset_index, mc_args = None, None, None, None
    tweakers, jvm_args = [], []
    for patch in patches:
        main_class = patch.get("mainClass", main_class)
        main_jar = patch.get("mainJar", main_jar)
        asset_index = patch.get("assetIndex", asset_index)
        mc_args = patch.get("minecraftArguments", mc_args)
        tweakers += patch.get("+tweakers", [])
        jvm_args += patch.get("+jvmArgs", [])

    classpath, natives = resolve_libraries(patches, pack_root, cache)
    jar_info = main_jar["downloads"]["artifact"]
    jar_name = main_jar["name"].replace(":", "-") + ".jar"
    classpath.append(download(jar_info["url"], cache / "versions" / jar_name,
                              jar_info.get("sha1")))
    assets = fetch_assets(asset_index, cache)

    natives_dir = cache / "natives"
    extract_natives(natives, natives_dir)

    substitutions = {
        "auth_player_name": "GCMIconExport",
        "version_name": "1.7.10",
        "game_directory": str(game_dir),
        "assets_root": str(assets),
        "assets_index_name": asset_index["id"],
        "auth_uuid": "00000000000000000000000000000000",
        "auth_access_token": "0",
        "user_properties": "{}",
        "user_type": "legacy",
    }
    game_args = [
        re.sub(r"\$\{(\w+)\}",
               lambda m: substitutions.get(m.group(1), m.group(0)), arg)
        for arg in mc_args.split()
    ]
    for tweaker in tweakers:
        game_args += ["--tweakClass", tweaker]

    return [
        "java",
        f"-Xms{args.min_memory}",
        f"-Xmx{args.max_memory}",
        *jvm_args,
        f"-Djava.library.path={natives_dir}",
        "-Dgcm.iconexport=true",
        f"-Dgcm.iconexport.iconSize={args.icon_size}",
        f"-Dgcm.iconexport.maxAnimationTicks={args.animation_ticks}",
        "-cp", ":".join(str(p) for p in classpath),
        main_class,
        *game_args,
    ]


def with_out_dir(command, out_dir):
    """The launch command, telling the mod where to write."""
    cp = command.index("-cp")
    return [*command[:cp], f"-Dgcm.iconexport.outDir={out_dir.resolve()}",
            *command[cp:]]


def run_game(command, game_dir, log_path, timeout):
    # lwjgl3ify writes a .desktop entry on first start and dies if there's
    # nowhere to put it (HOME=/tmp has no .local/share).
    xdg_data = game_dir.parent / "xdg-data"
    xdg_data.mkdir(exist_ok=True)
    env = dict(os.environ, SDL_AUDIODRIVER="dummy",
               XDG_DATA_HOME=str(xdg_data))
    xvfb = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24 -nolisten tcp"]
    log(f"Launching the client (timeout {timeout}s), log: {log_path}")
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            xvfb + command, cwd=game_dir, env=env, stdout=log_file,
            stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        position = 0
        try:
            while proc.poll() is None:
                time.sleep(5)
                with open(log_path, errors="replace") as f:
                    f.seek(position)
                    new = f.read()
                    position = f.tell()
                for line in new.splitlines():
                    if "[GCMIconExport]" in line:
                        log(f"game: {line.split(']:', 1)[-1].strip()}")
                    if FATAL_LOG.search(line):
                        log(f"Fatal error in game log: {line.strip()}")
                        stop(proc)
                        return 1
                if time.monotonic() > deadline:
                    log("Timed out.")
                    stop(proc)
                    return 124
        except KeyboardInterrupt:
            stop(proc)
            raise
    return proc.returncode


def stop(proc):
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(wait)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            continue


def collect_logs(game_dir, logs_out):
    crash_reports = sorted((game_dir / "crash-reports").glob("*.txt"))
    for src in [game_dir / "logs" / "fml-client-latest.log", *crash_reports]:
        if src.exists():
            logs_out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, logs_out / src.name)
    shutil.rmtree(game_dir / "crash-reports", ignore_errors=True)


def launch(command, game_dir, out_dir, logs_out, timeout, label):
    """One game run writing into out_dir; exits on failure."""
    code = run_game(with_out_dir(command, out_dir), game_dir,
                    logs_out / f"client-stdout{label}.log", timeout)
    collect_logs(game_dir, logs_out / "logs" / label.strip("-")
                 if label else logs_out / "logs")
    missing = [n for n in GAME_OUTPUTS if not (out_dir / n).exists()]
    if code != 0 or missing:
        log(f"Export failed (exit code {code}, missing: "
            f"{', '.join(missing) or 'none'}). Logs are in {logs_out}.")
        sys.exit(code or 1)
    return json.loads((out_dir / "export-report.json").read_text())


def animation_period(ticks):
    """The shortest loop that the captured per-tick frames repeat, seen at
    least twice; the whole capture if they never repeat within it."""
    for period in range(1, len(ticks) // 2 + 1):
        if all(ticks[i] == ticks[i - period] for i in range(period, len(ticks))):
            return ticks[:period]
    return ticks


# An animated icon bigger than this plays at half the frame rate (again
# if need be) until it fits. Big 3D renders that change completely every
# tick (Tectech's Forge of the Gods) came out at 1.5 MB otherwise.
APNG_MAX_BYTES = 400_000
APNG_MIN_FRAMES = 4

# Mean per-channel difference (0-255) under which a frame counts as back
# at frame 0, and how far the animation must have moved away before that.
NEAR_LOOP_MAX_DIFF = 1.0
NEAR_LOOP_MIN_TRAVEL = 8.0


def frame_difference(a, b):
    from PIL import ImageChops, ImageStat
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 4


def near_loop(ticks, image):
    """For captures that never repeat exactly (GT's transcendent metal
    turns 3.5 degrees a tick, so it's only exactly back after 720 ticks,
    but within half a degree after 103): the shortest prefix that ends
    where a frame is nearly frame 0 again, after moving well away from it.
    image(frame) gives a frame's RGBA image. None if there's no such
    point."""
    first = image(ticks[0])
    cache = {}

    def difference(frame):
        if frame not in cache:
            cache[frame] = frame_difference(first, image(frame))
        return cache[frame]

    travelled = 0.0
    for tick in range(1, len(ticks)):
        diff = difference(ticks[tick])
        if travelled >= NEAR_LOOP_MIN_TRAVEL and diff <= NEAR_LOOP_MAX_DIFF:
            # Close enough; carry on while it gets closer still, so the
            # seam is as small as the capture allows.
            while tick + 1 < len(ticks) and difference(ticks[tick + 1]) < diff:
                tick += 1
                diff = difference(ticks[tick])
            return ticks[:tick]
        travelled = max(travelled, diff)
    return None


def build_apng(frames, runs, tick_millis):
    """APNG bytes for [(frame image, ticks)] runs; frames maps frame -> PNG
    bytes. Pillow stores each frame after the first as just what changed.
    None if the frames aren't all the same size.

    Consecutive runs are distinct renders, so Pillow must keep every one. It
    merges frames it sees as identical, and Pillow before 10 compared them
    without alpha, silently turning alpha-only animations into stills."""
    import PIL
    from PIL import Image
    images = [Image.open(BytesIO(frames[frame])).convert("RGBA")
              for frame, _ in runs]
    if len({image.size for image in images}) > 1:
        # Drawn past the item box in some frames only (IconRenderer.BLEED):
        # an APNG's frames must all be one size.
        return None
    out = BytesIO()
    images[0].save(out, format="PNG", save_all=True,
                   append_images=images[1:], loop=0,
                   duration=[ticks * tick_millis for _, ticks in runs],
                   disposal=0, blend=0)
    written = getattr(Image.open(out), "n_frames", 1)
    if written != len(runs):
        raise RuntimeError(f"Pillow {PIL.__version__} wrote {written} of "
                           f"{len(runs)} animation frames")
    return out.getvalue()


def lookup_paths(lookup_path):
    """Every image path icons_lookup.json points at."""
    lookup = json.loads(lookup_path.read_text())
    return set().union(*(lookup.get(table, {}).values() for table in LOOKUP_TABLES))


def png_size(data):
    """(width, height) from a PNG's (or APNG's) header."""
    return struct.unpack(">II", data[16:24])


def finish_images(out_dir):
    """Rewrites images.zip with only the icons the lookup points at (the
    mod renders every stack, but NBT variants sharing a key are never
    shown), and the animated ones as APNGs. Returns how many are animated."""
    animated = build_animations(out_dir)
    keep = lookup_paths(out_dir / "icons_lookup.json")
    images = out_dir / "images.zip"
    tmp = images.with_name("images.zip.part")
    with zipfile.ZipFile(images) as src, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename in keep:
                dst.writestr(info.filename,
                             animated.get(info.filename) or src.read(info))
    tmp.replace(images)
    return len(animated)


def merge_runs(ticks, step=1):
    """[[frame, ticks]] runs, showing every step-th tick's frame for step
    ticks, so the animation keeps its speed at a lower frame rate."""
    runs = []
    for i in range(0, len(ticks), step):
        length = min(step, len(ticks) - i)
        if runs and runs[-1][0] == ticks[i]:
            runs[-1][1] += length
        else:
            runs.append([ticks[i], length])
    return runs


def build_within_budget(frames_zip, path, loop, tick_millis):
    """The loop as APNG bytes, at a lower frame rate if it's too big; None
    if it doesn't animate, or doesn't fit even with few frames."""
    step = 1
    while True:
        runs = merge_runs(loop, step)
        if len(runs) < 2:
            return None
        frames = {frame: frames_zip.read(f"{path}/{frame}.png")
                  for frame, _ in runs}
        apng = build_apng(frames, runs, tick_millis)
        if apng is None:
            log(f"{path}: frames of different sizes; keeping it still.")
            return None
        if len(apng) <= APNG_MAX_BYTES:
            return apng
        if len(runs) <= APNG_MIN_FRAMES:
            log(f"{path}: {len(apng)} bytes animated even at "
                f"{len(runs)} frames; keeping it still.")
            return None
        step *= 2


def build_animations(out_dir):
    """APNGs for the icons the mod captured animations for (animations.zip
    + animations.json, which are removed): {path: APNG bytes}."""
    anim_zip = out_dir / "animations.zip"
    anim_json = out_dir / "animations.json"
    if not (anim_zip.exists() and anim_json.exists()):
        return {}
    from PIL import Image
    spec = json.loads(anim_json.read_text())
    tick_millis = spec["tickMillis"]
    animated = {}
    with zipfile.ZipFile(anim_zip) as frames_zip:
        for path, runs in spec["icons"].items():
            ticks = [frame for frame, count in runs for _ in range(count)]
            loop = animation_period(ticks)
            if len(loop) == len(ticks):
                images = {}

                def image(frame, path=path):
                    if frame not in images:
                        images[frame] = Image.open(BytesIO(frames_zip.read(
                            f"{path}/{frame}.png"))).convert("RGBA")
                    return images[frame]

                loop = near_loop(ticks, image) or ticks
            apng = build_within_budget(frames_zip, path, loop, tick_millis)
            if apng:
                animated[path] = apng
    anim_zip.unlink()
    anim_json.unlink()
    return animated


def summary(report):
    return {k: v for k, v in report.items() if not isinstance(v, list)}


def merge_textures(default_zip, textured_zip, lookup_path, dest, credit):
    """images-<textures>.zip: the textured renders of every icon the
    lookup points at, falling back to the default render where the
    textured pass couldn't render one, or rendered it a different size (drawn
    past the item box in one pass only), which the lookup's bleed table
    wouldn't match. Returns how many fell back."""
    wanted = lookup_paths(lookup_path)
    fell_back = 0
    tmp = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(default_zip) as base, \
            zipfile.ZipFile(textured_zip) as textured, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as out:
        have = set(textured.namelist())
        out.writestr("CREDITS.txt", credit)
        for name in sorted(wanted):
            default = base.read(name)
            data = textured.read(name) if name in have else None
            if data and png_size(data) == png_size(default):
                out.writestr(name, data)
            else:
                out.writestr(name, default)
                fell_back += 1
    tmp.replace(dest)
    return fell_back


def write_manifest(out_dir, version_label, report, faithful=None):
    """data.json - what the server checks a downloaded bundle against."""
    names = [*GAME_OUTPUTS]
    textures = {"default": {"file": "images.zip", "name": "Default"}}
    if faithful:
        names.append(FAITHFUL["file"])
        textures["faithful32"] = faithful
    files = {}
    for name in names:
        path = out_dir / name
        files[name] = {"size": path.stat().st_size,
                       "sha256": file_hash(path, "sha256")}
    manifest = {
        "format": 1,
        "gtnh_version": version_label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exporter_commit": os.environ.get("GCM_EXPORTER_COMMIT") or None,
        "counts": summary(report),
        "textures": textures,
        "files": files,
    }
    (out_dir / "data.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack", type=Path, default=Path("/pack/pack.zip"),
                        help="GTNH MultiMC/Prism client zip")
    parser.add_argument("--out", type=Path, default=Path("/out"))
    parser.add_argument("--cache", type=Path, default=Path("/cache"))
    parser.add_argument("--work", type=Path, default=Path("/tmp/gcm-work"))
    parser.add_argument("--max-memory", default=env("GCM_MAX_MEMORY", "6G"))
    parser.add_argument("--min-memory", default="2G")
    parser.add_argument("--icon-size", type=int,
                        default=int(env("GCM_ICON_SIZE", "64")))
    parser.add_argument("--animation-ticks", type=int,
                        default=int(env("GCM_ANIMATION_TICKS", "160")),
                        help="game ticks of animation to capture (20/s); "
                        "0 = still icons only")
    parser.add_argument("--timeout", type=int,
                        default=int(env("GCM_TIMEOUT", "3600")))
    parser.add_argument("--version-label",
                        default=env("GCM_VERSION_LABEL") or "unknown",
                        help="GTNH version this pack is, e.g. 2.9.0-beta-3")
    parser.add_argument("--faithful", type=Path,
                        default=env("GCM_FAITHFUL_PACK") or None,
                        help="GTNH Faithful x32 resource pack zip; also "
                        "render icons with it")
    parser.add_argument("--faithful-version",
                        default=env("GCM_FAITHFUL_VERSION") or None,
                        help="its release (default: from the file name)")
    args = parser.parse_args()
    if args.faithful and not args.faithful.is_file():
        sys.exit(f"Resource pack not found: {args.faithful}")

    if not args.pack.is_file():
        sys.exit(f"Pack zip not found: {args.pack}")
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    for name in (*GAME_OUTPUTS, FAITHFUL["file"], "data.json",
                 "animations.zip", "animations.json",
                 "client-stdout.log", "client-stdout-faithful32.log"):
        (args.out / name).unlink(missing_ok=True)
    shutil.rmtree(args.out / "logs", ignore_errors=True)

    started = time.monotonic()
    pack_root, game_dir = extract_pack(args.pack, args.work)
    shutil.copy2(MOD_JAR, game_dir / "mods" / MOD_JAR.name)
    disable_angelica(game_dir)
    command = build_command(load_patches(pack_root), pack_root, game_dir,
                            args.cache, args)

    report = launch(command, game_dir, args.out, args.out, args.timeout, "")
    report["apngIcons"] = finish_images(args.out)
    (args.out / "export-report.json").write_text(
        json.dumps(report, indent=2) + "\n")
    log(f"Default textures: {json.dumps(summary(report))}")

    faithful = None
    if args.faithful:
        version = args.faithful_version or re.sub(
            r"^GTNH-Faithful-x32\.v?|\.zip$", "", args.faithful.name)
        log(f"Rendering again with {args.faithful.name}...")
        enable_resource_pack(game_dir, args.faithful)
        textured_out = args.work / "faithful32-out"
        shutil.rmtree(textured_out, ignore_errors=True)
        textured = launch(command, game_dir, textured_out, args.out,
                          args.timeout, "-faithful32")
        textured["apngIcons"] = finish_images(textured_out)
        credit = (f"{FAITHFUL['credit']}, version {version}.\n"
                  f"{FAITHFUL['url']}\n")
        fell_back = merge_textures(
            args.out / "images.zip", textured_out / "images.zip",
            args.out / "icons_lookup.json", args.out / FAITHFUL["file"],
            credit)
        faithful = {**FAITHFUL, "version": version,
                    "items_rendered": textured.get("itemsRendered"),
                    "animated_icons": textured["apngIcons"],
                    "default_fallbacks": fell_back}
        log(f"Faithful 32x: {json.dumps(summary(textured))}, "
            f"{fell_back} icons fell back to the default render")

    write_manifest(args.out, args.version_label, report, faithful)
    elapsed = round(time.monotonic() - started)
    log(f"Done in {elapsed}s: {json.dumps(summary(report))}")


if __name__ == "__main__":
    main()
