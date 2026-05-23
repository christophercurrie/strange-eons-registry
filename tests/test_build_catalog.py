import datetime

import build_catalog as bc


WHEN = datetime.datetime(2026, 5, 23, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _app_entry(tag: str, *, catalog_id: str | None = None) -> dict:
    version, _, suffix = tag.lstrip("v").partition("-")
    entry = {
        "tag": tag,
        "version": version,
        "suffix": suffix,
        "build": 1234,
        "type": "BETA" if suffix.startswith("beta") else "GENERAL",
        "released": "2026-05-08",
        "files": [],
    }
    if catalog_id:
        entry["catalog_id"] = catalog_id
    return entry


def test_merge_drops_suppressed_channel():
    # Fetcher signaled deliberate suppression: existing beta must NOT be carried forward.
    state = {"app": {"stable": _app_entry("v3.5.0"), "experimental": None}, "plugins": {}}
    existing = {
        "stable":       _app_entry("v3.4.0", catalog_id="CATALOGUEID{x:0-0-0-0-0-0-0}"),
        "experimental": _app_entry("v3.5.0-beta4",
                                   catalog_id="CATALOGUEID{y:0-0-0-0-0-0-0}"),
    }
    out = bc.merge(state, existing, WHEN)
    assert out["stable"]["tag"] == "v3.5.0"
    assert "experimental" not in out


def test_merge_preserves_channel_when_state_omits_it():
    # Fetcher made no claim about experimental (e.g. lookup failed): keep existing.
    state = {"app": {"stable": _app_entry("v3.5.0")}, "plugins": {}}
    existing = {
        "experimental": _app_entry("v3.5.0-beta4",
                                   catalog_id="CATALOGUEID{y:0-0-0-0-0-0-0}"),
    }
    out = bc.merge(state, existing, WHEN)
    assert out["experimental"]["tag"] == "v3.5.0-beta4"


def test_merge_reuses_catalog_id_when_tag_unchanged():
    state = {"app": {"stable": _app_entry("v3.5.0")}, "plugins": {}}
    existing = {
        "stable": _app_entry("v3.5.0", catalog_id="CATALOGUEID{x:keep-me}"),
    }
    out = bc.merge(state, existing, WHEN)
    assert out["stable"]["catalog_id"] == "CATALOGUEID{x:keep-me}"


def test_merge_assigns_new_catalog_id_when_tag_changes():
    state = {"app": {"stable": _app_entry("v3.5.0")}, "plugins": {}}
    existing = {
        "stable": _app_entry("v3.4.0", catalog_id="CATALOGUEID{x:old}"),
    }
    out = bc.merge(state, existing, WHEN)
    assert out["stable"]["catalog_id"] != "CATALOGUEID{x:old}"
    assert out["stable"]["catalog_id"].startswith(
        "CATALOGUEID{c8d1620e-5eeb-47f4-9ef2-49e9947faa90:")


def test_merge_plugins_overlay_existing():
    state = {
        "app": {},
        "plugins": {"uuid-a": {"filename": "new.seext"}},
    }
    existing = {
        "plugins": {
            "uuid-a": {"filename": "old.seext"},   # overwritten
            "uuid-b": {"filename": "kept.seext"},  # preserved
        },
    }
    out = bc.merge(state, existing, WHEN)
    assert out["plugins"]["uuid-a"]["filename"] == "new.seext"
    assert out["plugins"]["uuid-b"]["filename"] == "kept.seext"
