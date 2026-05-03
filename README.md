# strange-eons-registry

Drives the Strange Eons update catalog at <https://strangeeons.org/updates/>.

This repo is a small declarative registry: every entry in `registry.json`
maps to a GitHub repo (or, transitionally, a stable URL) that hosts a
`.seext` plugin bundle or the Strange Eons app installers. A scheduled
GitHub Actions workflow fetches the latest releases from each registered
source, rehosts them on the Strange Eons OpalStack server, and rebuilds
`updates/catalog.txt` and `manifest.json` so the in-app updater sees them.

## Adding a plugin

Open a PR adding an entry to `plugins[]` in `registry.json`. Either
shape works:

```json
{
  "name": "My Plugin",
  "url": "https://github.com/<owner>/<repo>/raw/refs/heads/main/MyPlugin.seext",
  "filename": "MyPlugin.seext"
}
```

```json
{
  "name": "My Plugin",
  "repo": "<owner>/<repo>",
  "asset": "MyPlugin.seext"
}
```

- `url` form: any stable HTTPS URL to your bundle. GitHub release
  download URLs (`/releases/latest/download/...`) and raw branch URLs
  both work. `filename` is the served name; must match what your
  plugin's `catalog-` keys expect to be downloaded as.
- `repo` form: for plugins published as GitHub Releases. The latest
  release is resolved via the API, the asset whose name matches `asset`
  is downloaded, and the resolved tag is recorded in `state.json`.
  `asset` doubles as the served filename.
- The cron rebuilds hourly, so updates land within an hour of you
  publishing.

The registry pulls the catalog metadata (id, name, description, version,
localized variants, etc.) directly from your bundle's `eons-plugin` file
— the same data the in-app catalog dialog already shows — so you don't
repeat any of that here.

## How releases flow

```
plugin author publishes new .seext
        ↓ (hourly cron, or repository_dispatch)
fetch_registered.py downloads bundles → bundle/
        ↓
build_catalog.py emits manifest.json + updates/catalog.txt + index.html
        ↓ (rsync over SSH)
strangeeons.org serves it
        ↓
in-app updater (catalog-url-1) sees the listing
```

App installers (Strange Eons itself) flow through the same registry: the
`app` section of `registry.json` points at the
`christophercurrie/strange-eons` repo, and the workflow picks the latest
stable + latest pre-release.

## Local testing

```
python3 scripts/fetch_registered.py --registry registry.json --output bundle
python3 scripts/build_catalog.py --bundle bundle --existing-manifest existing-manifest.json
```

`existing-manifest.json` can be `{}` for a clean run, or a copy of the
current server manifest if you want to test the legacy-plugin
preservation path.
