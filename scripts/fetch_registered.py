#!/usr/bin/env python3
"""Fetch latest releases for every entry in registry.json.

Produces a `bundle/` tree shaped like the OpalStack served root, plus
`bundle/state.json` describing what was fetched. `build_catalog.py`
consumes the state file together with the existing on-server manifest
to produce the final published artifacts.

bundle/
    state.json                                 fetched-release metadata
    <app-installer>...                         rehosted at server root
    updates/
        <plugin>.seext...                      rehosted next to catalog.txt
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

GH_API = "https://api.github.com"

# Hosts where attaching our GITHUB_TOKEN moves us from the shared anonymous
# rate limit (per egress IP) onto the per-user limit (~5000/hr authed).
_GH_AUTH_HOSTS = (
    "https://api.github.com/",
    "https://github.com/",
    "https://raw.githubusercontent.com/",
    "https://codeload.github.com/",
)

_TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}


def gh_request(url: str):
    data, _ = gh_request_full(url)
    return data


def gh_request_full(url: str):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "strange-eons-registry",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()), r.headers


def download(url: str, dest: Path, *, max_attempts: int = 4, base_delay: float = 2.0):
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "strange-eons-registry"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(_GH_AUTH_HOSTS):
        headers["Authorization"] = f"Bearer {token}"
        if url.startswith("https://api.github.com/"):
            headers["Accept"] = "application/octet-stream"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
            return dest
        except urllib.error.HTTPError as e:
            if e.code not in _TRANSIENT_HTTP or attempt == max_attempts:
                raise
            retry_after = e.headers.get("Retry-After") if e.headers else None
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = base_delay * (2 ** (attempt - 1))
            print(f"warn: HTTP {e.code} on {url}; retrying in {delay:.1f}s "
                  f"(attempt {attempt}/{max_attempts})", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"warn: network error on {url} ({e}); retrying in {delay:.1f}s "
                  f"(attempt {attempt}/{max_attempts})", file=sys.stderr)
        time.sleep(delay)
    return dest  # unreachable; keeps type checkers happy


def hash_and_size(path: Path):
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            sha.update(chunk)
            md5.update(chunk)
    return path.stat().st_size, sha.hexdigest(), md5.hexdigest()


# --- App release helpers --------------------------------------------------

def classify_type(suffix: str) -> str:
    s = suffix.lower()
    if not s:
        return "GENERAL"
    if s.startswith("alpha"):
        return "ALPHA"
    if s.startswith("beta"):
        return "BETA"
    return "DEVELOPMENT"


VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def parse_tag(tag: str):
    m = VERSION_RE.match(tag)
    if not m:
        raise ValueError(f"unrecognised tag {tag!r}")
    major, minor, patch, suffix = m.groups()
    return major, minor, patch, suffix or ""


_LAST_PAGE_RE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')


def compare_ahead(repo: str, base: str, head: str) -> int:
    try:
        data = gh_request(f"{GH_API}/repos/{repo}/compare/{base}...{head}")
        return data["ahead_by"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # Anchor tag isn't reachable (e.g., never pushed to the remote). Mirror
        # release.yml's fallback: count every commit reachable from head.
        print(f"warn: anchor {base!r} not found on {repo}; counting commits "
              f"reachable from {head!r} instead", file=sys.stderr)
        return count_commits(repo, head)


def count_commits(repo: str, sha: str) -> int:
    """Total commits reachable from sha. Uses page=last on a per_page=1 listing."""
    data, headers = gh_request_full(
        f"{GH_API}/repos/{repo}/commits?sha={sha}&per_page=1")
    link = headers.get("Link", "")
    m = _LAST_PAGE_RE.search(link)
    if m:
        return int(m.group(1))
    # No Link header means we already have the only page; one or zero commits.
    return len(data)


def pick_app_releases(repo: str):
    """Return (latest_stable, latest_prerelease). Either may be None."""
    releases = gh_request(f"{GH_API}/repos/{repo}/releases?per_page=30")
    stable = next((r for r in releases if not r["draft"] and not r["prerelease"]), None)
    pre = next((r for r in releases if not r["draft"] and r["prerelease"]), None)
    return stable, pre


def match_assets(release: dict, globs: list) -> list:
    """Return [(asset, label)] for assets matching any glob."""
    out = []
    for asset in release.get("assets", []):
        for g in globs:
            if fnmatch.fnmatch(asset["name"], g["glob"]):
                out.append((asset, g["label"]))
                break
    return out


def collect_app_channel(repo: str, anchor_tag: str, offset: int,
                        release: dict, globs: list, output_dir: Path):
    if release is None:
        return None
    tag = release["tag_name"]
    major, minor, patch, suffix = parse_tag(tag)
    version = f"{major}.{minor}.{patch}"
    ahead = compare_ahead(repo, anchor_tag, tag)
    build = offset + ahead
    files = []
    for asset, label in match_assets(release, globs):
        dest = output_dir / asset["name"]
        print(f"  app[{tag}]: downloading {asset['name']}")
        download(asset["browser_download_url"], dest)
        size, sha, md5 = hash_and_size(dest)
        files.append({
            "label": label,
            "filename": asset["name"],
            "size": size,
            "sha256": sha,
            "md5": md5,
        })
    if not files:
        print(f"warn: no assets matched in release {tag!r}", file=sys.stderr)
        return None
    return {
        "tag": tag,
        "version": version,
        "suffix": suffix,
        "build": build,
        "type": classify_type(suffix),
        "released": (release.get("published_at") or release.get("created_at") or "")[:10],
        "files": files,
    }


# --- Plugin helpers -------------------------------------------------------

_CATALOG_ID_RE = re.compile(
    r'^id\s*[=:]\s*(CATALOGUEID\{([0-9a-fA-F-]+):[\d-]+\})', re.M)
_KEY_RE = re.compile(r'^(\s*)catalog-(\S+?)(\s*[=:]\s*)(.*)$')


def parse_catalog_id(eons_plugin: str):
    m = _CATALOG_ID_RE.search(eons_plugin)
    if not m:
        raise ValueError("no CATALOGUEID found in eons-plugin file")
    return m.group(1), m.group(2).lower()


def extract_catalog_block(eons_plugin: str) -> str:
    out = []
    lines = eons_plugin.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _KEY_RE.match(line)
        if m:
            prefix, key, sep, rest = m.groups()
            out.append(f"{prefix}{key}{sep}{rest}")
            while lines[i].rstrip().endswith("\\") and not lines[i].rstrip().endswith("\\\\"):
                i += 1
                if i < len(lines):
                    out.append(lines[i])
            i += 1
        else:
            i += 1
    return "\n".join(out)


def collect_plugin(plugin: dict, output_dir: Path):
    filename = plugin["filename"]
    dest = output_dir / "updates" / filename
    print(f"  plugin[{plugin.get('name', filename)}]: downloading {plugin['url']}")
    download(plugin["url"], dest)
    size, sha, md5 = hash_and_size(dest)
    with zipfile.ZipFile(dest) as z:
        eons_plugin = z.read("eons-plugin").decode("utf-8")
    catalog_id, uuid = parse_catalog_id(eons_plugin)
    catalog_block = extract_catalog_block(eons_plugin)
    if not catalog_block:
        print(f"warn: no catalog-* keys in {filename}; entry will be sparse",
              file=sys.stderr)
    return uuid, {
        "filename": filename,
        "size": size,
        "md5": md5,
        "sha256": sha,
        "catalog_id": catalog_id,
        "catalog_block": catalog_block,
    }


# --- Driver ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", required=True, type=Path)
    ap.add_argument("--output",   required=True, type=Path)
    args = ap.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)

    state = {"app": {}, "plugins": {}}

    app = registry.get("app")
    if app:
        try:
            stable, pre = pick_app_releases(app["repo"])
        except urllib.error.HTTPError as e:
            print(f"warn: app releases lookup failed ({e}); skipping app section",
                  file=sys.stderr)
            stable = pre = None
        for channel, release in (("stable", stable), ("experimental", pre)):
            entry = collect_app_channel(
                app["repo"], app["anchor_tag"], app["build_offset"],
                release, app["asset_globs"], args.output)
            if entry:
                state["app"][channel] = entry

    plugins = registry.get("plugins", [])
    failures = []
    for plugin in plugins:
        name = plugin.get("name") or plugin.get("filename") or plugin.get("url", "?")
        try:
            uuid, entry = collect_plugin(plugin, args.output)
        except Exception as e:
            print(f"warn: failed to fetch {name!r}: {e}; existing manifest entry "
                  f"(if any) will be preserved", file=sys.stderr)
            failures.append(name)
            continue
        state["plugins"][uuid] = entry

    (args.output / "state.json").write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Fetched {len(state['app'])} app channel(s), {len(state['plugins'])} plugin(s).")
    if failures:
        print(f"warn: {len(failures)} of {len(plugins)} plugin(s) failed: "
              f"{', '.join(failures)}", file=sys.stderr)
        # Hard-fail only when nothing succeeded — build_catalog.py preserves
        # stale entries from the existing manifest, so a partial fetch still
        # produces a valid catalog and isn't worth waking the maintainer over.
        if plugins and not state["plugins"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
