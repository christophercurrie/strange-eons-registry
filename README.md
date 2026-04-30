# strange-eons-registry

Drives the Strange Eons update catalog at <https://strangeeons.org/updates/>.

This repo is a small declarative registry: every entry in `registry.json`
maps to a GitHub repo (or, transitionally, a stable URL) that hosts a
`.seext` plugin bundle or the Strange Eons app installers. A scheduled
GitHub Actions workflow fetches the latest releases from each registered
source, rehosts them on the Strange Eons OpalStack server, and rebuilds
`updates/catalog.txt` and `manifest.json` so the in-app updater sees them.

## Adding a plugin

Open a PR adding an entry to `plugins[]` in `registry.json`:

```json
{
  "name": "My Plugin",
  "url": "https://github.com/<owner>/<repo>/releases/latest/download/MyPlugin.seext",
  "filename": "MyPlugin.seext"
}
```

- `url`: any stable HTTPS URL to your `.seext` bundle. GitHub release
  download URLs (`/releases/latest/download/...`) and raw branch URLs
  both work. The cron rebuilds hourly, so updates land within an hour
  of you publishing.
- `filename`: the name the bundle will be served as. Must match what
  your plugin's `catalog-` keys expect to be downloaded as.

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
