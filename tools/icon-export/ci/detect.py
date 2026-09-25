#!/usr/bin/env python3
"""
Decides which GTNH versions .github/workflows/gtnh-data.yml builds.

Scheduled runs: the most recent stable, beta and RC releases of
GTNewHorizons/GT-New-Horizons-Modpack (nightlies excluded) that don't have
a gtnh-data-<version> release in the data repo yet (DATA_REPO, falling back
to this repo for forks without one), and whose MultiMC client
zip can be found on downloads.gtnewhorizons.com. A version whose zip isn't
up yet is skipped and picked up again on a later run.

Manual runs (INPUT_VERSION set): just that version, using INPUT_PACK_URL
if given, even if it's already published when INPUT_FORCE is "true".

Writes `matrix` (JSON, {"include": [{"version", "pack_url"}]}) and `count`
to $GITHUB_OUTPUT. Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request

GTNH_REPO = "GTNewHorizons/GT-New-Horizons-Modpack"
DOWNLOADS = "https://downloads.gtnewhorizons.com/Multi_mc_downloads/"
TAG_PREFIX = "gtnh-data-"
RECENT_RELEASES = 10
MAX_BUILDS_PER_RUN = 3
# The Java suffix in the file name has changed between releases.
JAVA_SUFFIXES = ("17-25", "17-26", "17-21")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def github_json(url):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "gcm-gtnh-data"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def github_releases(repo, pages=5):
    """Up to pages * 100 releases. GTNH publishes a nightly most days, so
    the last stable release can be several pages back."""
    releases = []
    for page in range(1, pages + 1):
        batch = github_json(f"https://api.github.com/repos/{repo}"
                            f"/releases?per_page=100&page={page}")
        releases += batch
        if len(batch) < 100:
            break
    return releases


def gtnh_versions(releases):
    """Newest first: stable, beta and RC tags, no nightlies or drafts."""
    ordered = sorted(
        (r for r in releases
         if not r.get("draft") and "nightly" not in r["tag_name"].lower()),
        key=lambda r: r.get("published_at") or "", reverse=True)
    return [r["tag_name"] for r in ordered][:RECENT_RELEASES]


def published_versions(releases):
    return {r["tag_name"][len(TAG_PREFIX):] for r in releases
            if r["tag_name"].startswith(TAG_PREFIX)}


def is_prerelease(version):
    lowered = version.lower()
    return "beta" in lowered or "rc" in lowered


def candidate_urls(version):
    """Stable packs sit at the top level, betas and RCs under betas/."""
    dirs = ("betas/", "") if is_prerelease(version) else ("", "betas/")
    return [f"{DOWNLOADS}{d}GT_New_Horizons_{version}_Java_{java}.zip"
            for d in dirs for java in JAVA_SUFFIXES]


def url_exists(url):
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "gcm-gtnh-data"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def find_pack_url(version, exists=url_exists):
    for url in candidate_urls(version):
        if exists(url):
            return url
    return None


def plan(gtnh_releases, our_releases, exists=url_exists,
         version=None, pack_url=None, force=False):
    published = published_versions(our_releases)
    if version:
        if version in published and not force:
            log(f"{version} is already published; set force to rebuild it.")
            return []
        url = pack_url or find_pack_url(version, exists)
        if not url:
            raise SystemExit(f"No client zip found for {version}; "
                             "pass pack_url.")
        return [{"version": version, "pack_url": url}]

    builds = []
    for candidate in gtnh_versions(gtnh_releases):
        if candidate in published:
            continue
        url = find_pack_url(candidate, exists)
        if not url:
            log(f"{candidate}: no client zip on the downloads site yet.")
            continue
        builds.append({"version": candidate, "pack_url": url})
        if len(builds) == MAX_BUILDS_PER_RUN:
            break
    return builds


def main():
    repo = os.environ.get("DATA_REPO") or os.environ["GITHUB_REPOSITORY"]
    builds = plan(
        github_releases(GTNH_REPO),
        github_releases(repo),
        version=os.environ.get("INPUT_VERSION") or None,
        pack_url=os.environ.get("INPUT_PACK_URL") or None,
        force=os.environ.get("INPUT_FORCE") == "true",
    )
    for build in builds:
        log(f"Will build {build['version']} from {build['pack_url']}")
    if not builds:
        log("Nothing to build.")
    output = os.environ.get("GITHUB_OUTPUT")
    lines = f"matrix={json.dumps({'include': builds})}\ncount={len(builds)}\n"
    if output:
        with open(output, "a") as f:
            f.write(lines)
    else:
        print(lines, end="")


if __name__ == "__main__":
    main()
