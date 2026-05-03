import urllib.error
from pathlib import Path
from zipfile import ZipFile

import pytest

import fetch_registered as fr


# --- pure helpers -------------------------------------------------------

def test_parse_tag_release():
    assert fr.parse_tag("v3.5.0") == ("3", "5", "0", "")


def test_parse_tag_prerelease():
    assert fr.parse_tag("v3.5.0-alpha2") == ("3", "5", "0", "alpha2")


def test_parse_tag_invalid():
    with pytest.raises(ValueError):
        fr.parse_tag("3.5.0")


@pytest.mark.parametrize("suffix,expected", [
    ("",        "GENERAL"),
    ("alpha2",  "ALPHA"),
    ("beta1",   "BETA"),
    ("dev3",    "DEVELOPMENT"),
    ("rc1",     "DEVELOPMENT"),
])
def test_classify_type(suffix, expected):
    assert fr.classify_type(suffix) == expected


def test_parse_catalog_id():
    eons = "id = CATALOGUEID{abc-123:2024-01-01-00-00-00-000}\nfoo = bar\n"
    cid, uuid = fr.parse_catalog_id(eons)
    assert cid == "CATALOGUEID{abc-123:2024-01-01-00-00-00-000}"
    assert uuid == "abc-123"


def test_parse_catalog_id_missing():
    with pytest.raises(ValueError):
        fr.parse_catalog_id("no id here")


def test_extract_catalog_block_filters_to_catalog_keys():
    eons = (
        "id = X\n"
        "catalog-name = Foo\n"
        "catalog-description = Bar\n"
        "irrelevant = baz\n"
    )
    out = fr.extract_catalog_block(eons)
    assert "name = Foo" in out
    assert "description = Bar" in out
    assert "irrelevant" not in out
    assert "id = X" not in out


def test_extract_catalog_block_continuation_lines():
    eons = (
        "catalog-description = line1 \\\n"
        "line2\n"
        "catalog-name = Foo\n"
    )
    out = fr.extract_catalog_block(eons)
    assert "line1 \\" in out
    assert "line2" in out
    assert "name = Foo" in out


def test_match_assets():
    release = {"assets": [
        {"name": "se-3.5.0-macos-aarch64.dmg"},
        {"name": "se-3.5.0-windows.msi"},
        {"name": "checksums.txt"},
    ]}
    globs = [
        {"glob": "*-macos-aarch64.dmg", "label": "macOS (Apple Silicon)"},
        {"glob": "*-windows.msi",       "label": "Windows"},
    ]
    pairs = sorted((a["name"], lbl) for a, lbl in fr.match_assets(release, globs))
    assert pairs == [
        ("se-3.5.0-macos-aarch64.dmg", "macOS (Apple Silicon)"),
        ("se-3.5.0-windows.msi",       "Windows"),
    ]


# --- resolve_plugin_source ---------------------------------------------

def test_resolve_plugin_source_url_shape():
    plugin = {"url": "https://example.com/foo.seext", "filename": "foo.seext"}
    assert fr.resolve_plugin_source(plugin) == (
        "https://example.com/foo.seext", "foo.seext", None,
    )


def test_resolve_plugin_source_repo_shape(monkeypatch):
    fake = {
        "tag_name": "v1.2.3",
        "assets": [
            {"name": "Other.zip",      "browser_download_url": "https://x/other"},
            {"name": "MyPlugin.seext", "browser_download_url": "https://x/my"},
        ],
    }
    monkeypatch.setattr(fr, "gh_request", lambda url: fake)
    plugin = {"repo": "owner/repo", "asset": "MyPlugin.seext"}
    assert fr.resolve_plugin_source(plugin) == (
        "https://x/my", "MyPlugin.seext", "v1.2.3",
    )


def test_resolve_plugin_source_repo_missing_asset(monkeypatch):
    fake = {"tag_name": "v1.2.3", "assets": [
        {"name": "Other.zip", "browser_download_url": "https://x/other"},
    ]}
    monkeypatch.setattr(fr, "gh_request", lambda url: fake)
    with pytest.raises(ValueError, match="MyPlugin.seext"):
        fr.resolve_plugin_source({"repo": "o/r", "asset": "MyPlugin.seext"})


# --- collect_plugin -----------------------------------------------------

def _make_seplugin(path: Path, eons_plugin: str) -> None:
    with ZipFile(path, "w") as z:
        z.writestr("eons-plugin", eons_plugin)


def _stub_download(monkeypatch, source: Path):
    def fake(url, dest, **_kw):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        return dest
    monkeypatch.setattr(fr, "download", fake)


def test_collect_plugin_repo_shape_records_tag(tmp_path, monkeypatch):
    eons = (
        "id = CATALOGUEID{12345678-1234-1234-1234-123456789012:2024-01-01-00-00-00-000}\n"
        "catalog-name = Test\n"
        "catalog-description = test plugin\n"
    )
    src = tmp_path / "src.seplugin"
    _make_seplugin(src, eons)

    fake = {
        "tag_name": "v9.9.9",
        "assets": [{"name": "Test.seplugin",
                    "browser_download_url": "https://example.com/Test.seplugin"}],
    }
    monkeypatch.setattr(fr, "gh_request", lambda url: fake)
    _stub_download(monkeypatch, src)

    uuid, entry = fr.collect_plugin(
        {"name": "T", "repo": "o/r", "asset": "Test.seplugin"}, tmp_path / "out")

    assert uuid == "12345678-1234-1234-1234-123456789012"
    assert entry["filename"] == "Test.seplugin"
    assert entry["tag"] == "v9.9.9"
    assert "name = Test" in entry["catalog_block"]


def test_collect_plugin_url_shape_omits_tag(tmp_path, monkeypatch):
    eons = (
        "id = CATALOGUEID{abc-001:2024-01-01-00-00-00-000}\n"
        "catalog-name = U\n"
    )
    src = tmp_path / "src.seplugin"
    _make_seplugin(src, eons)
    _stub_download(monkeypatch, src)

    uuid, entry = fr.collect_plugin(
        {"name": "U", "url": "https://x/u.seext", "filename": "u.seext"},
        tmp_path / "out")

    assert uuid == "abc-001"
    assert "tag" not in entry


# --- compare_ahead -----------------------------------------------------

def test_compare_ahead_normal(monkeypatch):
    monkeypatch.setattr(fr, "gh_request", lambda url: {"ahead_by": 17})
    assert fr.compare_ahead("o/r", "v0.0.0", "v1.0.0") == 17


def test_compare_ahead_falls_back_on_404(monkeypatch):
    def fail_compare(url):
        if "/compare/" in url:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        raise AssertionError("unexpected url: " + url)
    monkeypatch.setattr(fr, "gh_request", fail_compare)
    monkeypatch.setattr(fr, "count_commits", lambda repo, sha: 42)
    assert fr.compare_ahead("o/r", "v0.0.0", "v1.0.0") == 42


def test_compare_ahead_propagates_non_404(monkeypatch):
    def boom(url):
        raise urllib.error.HTTPError(url, 500, "server error", {}, None)
    monkeypatch.setattr(fr, "gh_request", boom)
    with pytest.raises(urllib.error.HTTPError):
        fr.compare_ahead("o/r", "v0.0.0", "v1.0.0")


# --- count_commits ------------------------------------------------------

def test_count_commits_with_link_header(monkeypatch):
    headers = {"Link": (
        '<https://api.github.com/repos/o/r/commits?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/commits?per_page=1&page=137>; rel="last"'
    )}
    monkeypatch.setattr(fr, "gh_request_full", lambda url: ([{}], headers))
    assert fr.count_commits("o/r", "abc") == 137


def test_count_commits_single_page(monkeypatch):
    monkeypatch.setattr(fr, "gh_request_full",
                        lambda url: ([{"sha": "abc"}], {}))
    assert fr.count_commits("o/r", "abc") == 1


def test_count_commits_empty(monkeypatch):
    monkeypatch.setattr(fr, "gh_request_full", lambda url: ([], {}))
    assert fr.count_commits("o/r", "abc") == 0


# --- collect_app_channel ----------------------------------------------

def test_collect_app_channel_full(tmp_path, monkeypatch):
    release = {
        "tag_name": "v3.5.0-alpha2",
        "published_at": "2026-04-15T12:00:00Z",
        "assets": [
            {"name": "se-3.5.0-alpha2-macos-aarch64.dmg",
             "browser_download_url": "https://example.com/mac"},
            {"name": "se-3.5.0-alpha2-windows.msi",
             "browser_download_url": "https://example.com/win"},
            {"name": "checksums.txt",
             "browser_download_url": "https://example.com/cs"},
        ],
    }
    monkeypatch.setattr(fr, "compare_ahead", lambda repo, base, head: 5)

    def fake_download(url, dest, **_kw):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"data:" + url.encode())
        return dest
    monkeypatch.setattr(fr, "download", fake_download)

    globs = [
        {"glob": "*-macos-aarch64.dmg", "label": "macOS (Apple Silicon)"},
        {"glob": "*-windows.msi",       "label": "Windows"},
    ]
    out = fr.collect_app_channel(
        "o/r", "v3.5.0-alpha1", 5000, release, globs, tmp_path)

    assert out["tag"]      == "v3.5.0-alpha2"
    assert out["version"]  == "3.5.0"
    assert out["suffix"]   == "alpha2"
    assert out["build"]    == 5005
    assert out["type"]     == "ALPHA"
    assert out["released"] == "2026-04-15"
    assert sorted(f["filename"] for f in out["files"]) == [
        "se-3.5.0-alpha2-macos-aarch64.dmg",
        "se-3.5.0-alpha2-windows.msi",
    ]


def test_collect_app_channel_none_release():
    assert fr.collect_app_channel(
        "o/r", "v1.0.0", 0, None, [], Path("/tmp")) is None


def test_collect_app_channel_no_matching_assets(tmp_path, monkeypatch):
    release = {
        "tag_name": "v1.0.0",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [{"name": "unrelated.txt",
                    "browser_download_url": "https://x/u"}],
    }
    monkeypatch.setattr(fr, "compare_ahead", lambda *a: 1)
    out = fr.collect_app_channel(
        "o/r", "v0.9.0", 0, release,
        [{"glob": "*.dmg", "label": "macOS"}], tmp_path)
    assert out is None


# --- pick_app_releases --------------------------------------------------

def test_pick_app_releases(monkeypatch):
    releases = [
        {"draft": True,  "prerelease": False, "tag_name": "v3.0.0-draft"},
        {"draft": False, "prerelease": True,  "tag_name": "v3.0.0-beta1"},
        {"draft": False, "prerelease": False, "tag_name": "v2.9.0"},
        {"draft": False, "prerelease": True,  "tag_name": "v2.8.0-beta3"},
        {"draft": False, "prerelease": False, "tag_name": "v2.8.0"},
    ]
    monkeypatch.setattr(fr, "gh_request", lambda url: releases)
    stable, pre = fr.pick_app_releases("o/r")
    assert stable["tag_name"] == "v2.9.0"
    assert pre["tag_name"]    == "v3.0.0-beta1"


def test_pick_app_releases_empty(monkeypatch):
    monkeypatch.setattr(fr, "gh_request", lambda url: [])
    stable, pre = fr.pick_app_releases("o/r")
    assert stable is None and pre is None


# --- download retry ----------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self._sent = False

    def read(self, _n):
        if self._sent:
            return b""
        self._sent = True
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_download_retries_on_transient(tmp_path, monkeypatch):
    calls = {"n": 0}

    def urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "transient", {}, None)
        return _FakeResponse(b"payload")

    monkeypatch.setattr(fr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fr.time, "sleep", lambda *_: None)

    dest = tmp_path / "out.bin"
    fr.download("https://example.com/x", dest, max_attempts=3, base_delay=0.0)
    assert dest.read_bytes() == b"payload"
    assert calls["n"] == 2


def test_download_does_not_retry_on_404(tmp_path, monkeypatch):
    calls = {"n": 0}

    def urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(fr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fr.time, "sleep", lambda *_: None)

    with pytest.raises(urllib.error.HTTPError):
        fr.download("https://example.com/x", tmp_path / "out.bin",
                    max_attempts=5, base_delay=0.0)
    assert calls["n"] == 1


def test_download_gives_up_after_max_attempts(tmp_path, monkeypatch):
    calls = {"n": 0}

    def urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 503, "transient", {}, None)

    monkeypatch.setattr(fr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fr.time, "sleep", lambda *_: None)

    with pytest.raises(urllib.error.HTTPError):
        fr.download("https://example.com/x", tmp_path / "out.bin",
                    max_attempts=2, base_delay=0.0)
    assert calls["n"] == 2
