#!/usr/bin/env python3
"""Assemble manifest.json + updates/catalog.txt + index.html.

Reads `bundle/state.json` (produced by fetch_registered.py) and the
existing on-server manifest. Plugins listed in registry.json overwrite
the matching existing entries; plugins NOT in registry.json are
preserved verbatim (so legacy entries keep working until their author
onboards). App stable/experimental are fully owned by the registry once
an app release exists, but if no current release is fetched for a given
channel, the existing entry is preserved.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

CHANNEL_UUIDS = {
    "stable":       "c8d1620e-5eeb-47f4-9ef2-49e9947faa90",
    "experimental": "1b7ef4bd-f63a-4884-9979-830d4feb18b8",
}
SITE_URL = "https://strangeeons.org/"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def gen_catalog_id(channel: str, when: datetime.datetime) -> str:
    """CATALOGUEID{uuid:Y-M-D-H-M-S-MS}. Java Calendar months are 0-indexed."""
    uuid = CHANNEL_UUIDS[channel]
    date = (
        f"{when.year}-{when.month - 1}-{when.day}-"
        f"{when.hour}-{when.minute}-{when.second}-"
        f"{when.microsecond // 1000}"
    )
    return f"CATALOGUEID{{{uuid}:{date}}}"


def derive_existing_tag(entry: dict) -> str | None:
    if not entry:
        return None
    if entry.get("tag"):
        return entry["tag"]
    version = entry.get("version")
    if not version:
        return None
    suffix = entry.get("suffix") or ""
    return f"v{version}{('-' + suffix) if suffix else ''}"


# --- Listing rendering ---------------------------------------------------

def format_app_listing(entry: dict) -> str:
    suffix = entry.get("suffix") or ""
    version_label = f"{entry['version']}{('-' + suffix) if suffix else ''}"
    return (
        f"name = Strange Eons {version_label}\n"
        f"description = Update to Strange Eons {version_label} (build {entry['build']})\n"
        f"version = {entry['build']}\n"
        f"url = {SITE_URL}\n"
        f"homepage = {SITE_URL}\n"
        f"date = {entry['released']}\n"
        f"id = {entry['catalog_id']}\n"
        f"hidden = yes\n"
    )


def format_plugin_listing(entry: dict) -> str:
    return (
        f"url = {entry['filename']}\n"
        f"{entry['catalog_block']}\n"
        f"size = {entry['size']}\n"
        f"md5 = {entry['md5']}\n"
        f"id = {entry['catalog_id']}\n"
    )


def write_catalog(output_dir: Path, manifest: dict, when: datetime.datetime):
    updates_dir = output_dir / "updates"
    updates_dir.mkdir(exist_ok=True)
    listings = []
    for channel in ("stable", "experimental"):
        entry = manifest.get(channel)
        if entry and entry.get("catalog_id"):
            listings.append(format_app_listing(entry))
    plugins = manifest.get("plugins", {})
    for uuid in sorted(plugins.keys()):
        listings.append(format_plugin_listing(plugins[uuid]))
    if not listings:
        body = (
            f"# Strange Eons combined catalog\n"
            f"# Generated {when.isoformat()}\n"
            f"# (no listings published yet)\n"
        )
    else:
        body = (
            f"# Strange Eons combined catalog\n"
            f"# Generated {when.isoformat()}\n"
            f"\n"
            + "\n".join(listings)
        )
    (updates_dir / "catalog.txt").write_text(body, encoding="utf-8")
    n_app = sum(1 for c in ("stable", "experimental")
                if manifest.get(c) and manifest[c].get("catalog_id"))
    print(f"  catalog: updates/catalog.txt ({n_app} app, {len(plugins)} plugin)")


INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Strange Eons</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
    h1 {{ margin-bottom: 0.25rem; }}
    h2 {{ margin-top: 2rem; }}
    .ver {{ color: #666; margin-bottom: 0.5rem; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; }}
    th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }}
    th {{ background: #f7f7f7; }}
    code {{ font-family: ui-monospace, monospace; font-size: 0.85em; word-break: break-all; }}
    .empty {{ color: #888; font-style: italic; }}
    a.docs {{ display: inline-block; margin-top: 1rem; }}
    .github-ribbon {{ position: fixed; top: 0; right: 0; border: 0; z-index: 1000; }}
  </style>
</head>
<body>
  <a href="https://github.com/christophercurrie/strange-eons">
    <img class="github-ribbon"
         src="https://github.blog/wp-content/uploads/2008/12/forkme_right_darkblue_121621.png"
         alt="Fork me on GitHub"
         loading="lazy" decoding="async" width="149" height="149">
  </a>
  <h1>Strange Eons</h1>
{sections}
  <a class="docs" href="https://strangeeons.cgjennings.ca/">Documentation (upstream)</a>
</body>
</html>
"""

SECTION_TEMPLATE = """  <h2>{heading}</h2>
  <p class="ver">Version {version_label} &middot; build {build} &middot; released {date}</p>
  <table>
    <thead><tr><th>Platform</th><th>File</th><th>Size</th><th>SHA-256</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
"""

EMPTY_SECTION_TEMPLATE = """  <h2>{heading}</h2>
  <p class="empty">No {heading_lower} release published yet.</p>
"""


def render_section(heading: str, entry: dict | None) -> str:
    if not entry or not entry.get("files"):
        return EMPTY_SECTION_TEMPLATE.format(heading=heading,
                                             heading_lower=heading.lower())
    rows = []
    for it in entry["files"]:
        rows.append(
            f"      <tr>"
            f"<td>{it['label']}</td>"
            f"<td><a href=\"{it['filename']}\">{it['filename']}</a></td>"
            f"<td>{human_size(it['size'])}</td>"
            f"<td><code>{it['sha256']}</code></td>"
            f"</tr>"
        )
    suffix = entry.get("suffix") or ""
    version_label = f"{entry['version']}{('-' + suffix) if suffix else ''}"
    return SECTION_TEMPLATE.format(
        heading=heading,
        version_label=version_label,
        build=entry["build"],
        date=entry["released"],
        rows="\n".join(rows),
    )


def write_index(output_dir: Path, manifest: dict):
    sections = (
        render_section("Stable", manifest.get("stable"))
        + render_section("Pre-release", manifest.get("experimental"))
    )
    (output_dir / "index.html").write_text(
        INDEX_TEMPLATE.format(sections=sections), encoding="utf-8")
    stable_n = len(manifest.get("stable", {}).get("files", []))
    exp_n = len(manifest.get("experimental", {}).get("files", []))
    print(f"  index: index.html (stable: {stable_n}, pre-release: {exp_n})")


def write_manifest(output_dir: Path, manifest: dict):
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  manifest: manifest.json ({len([k for k in manifest if k != 'plugins'])} channel(s), "
          f"{len(manifest.get('plugins', {}))} plugin(s))")


# --- Merge ---------------------------------------------------------------

def load_json(path: Path | None):
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"warn: failed to parse {path} ({e}); treating as empty", file=sys.stderr)
        return {}


def merge(state: dict, existing: dict, when: datetime.datetime) -> dict:
    out = {"plugins": {}}

    for channel in ("stable", "experimental"):
        new = state.get("app", {}).get(channel)
        old = existing.get(channel)
        if new:
            old_tag = derive_existing_tag(old)
            if old_tag == new["tag"] and old and old.get("catalog_id"):
                new["catalog_id"] = old["catalog_id"]
            else:
                new["catalog_id"] = gen_catalog_id(channel, when)
            out[channel] = new
        elif old:
            out[channel] = old

    out["plugins"] = dict(existing.get("plugins", {}))
    out["plugins"].update(state.get("plugins", {}))

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle",            required=True, type=Path,
                    help="Output directory produced by fetch_registered.py")
    ap.add_argument("--existing-manifest", required=True, type=Path,
                    help="Path to current on-server manifest.json (or empty file)")
    args = ap.parse_args()

    when = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    state = load_json(args.bundle / "state.json")
    existing = load_json(args.existing_manifest)

    manifest = merge(state, existing, when)

    args.bundle.mkdir(parents=True, exist_ok=True)
    write_manifest(args.bundle, manifest)
    write_catalog(args.bundle, manifest, when)
    write_index(args.bundle, manifest)
    print(f"Done. Output: {args.bundle}")


if __name__ == "__main__":
    main()
