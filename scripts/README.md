# Scripts

## Grafana Dashboard Utility
`grafana_dashboard.py` provides lightweight CLI operations for Grafana dashboards.
It supports downloading, uploading, listing, and basic discovery commands.

### Requirements
- `pip install requests`
- `GRAFANA_TOKEN` environment variable (or use `--token`)

### Download a dashboard
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  download --uid abcdEFGh
```

By name (optionally scoped to a folder id):
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  download --name "Core Metrics" --folder-id 3
```

Optional output payload for POST updates:
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  download --uid abcdEFGh \
  --out-payload dashboard_abcdEFGh.payload.json
```

### Upload a dashboard
Upload a ready payload:
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  upload --payload dashboard_abcdEFGh.payload.json
```

Wrap a dashboard JSON directly:
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  upload --dashboard dashboard_abcdEFGh.json --message "Update via API"
```

### List dashboards
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  list --query "network" --tag prod
```

### List folders
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  folders --query "team" --limit 50
```

### Get a folder by UID
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  folder --uid abcdEFGh
```

### List tags
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  tags --query "cpu"
```

### Get dashboard permissions
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  permissions --name "Core Metrics"
```

### Get folder permissions
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  folder-permissions --uid abcdEFGh
```

### List dashboard versions
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  versions --uid abcdEFGh --limit 20
```

### Delete a dashboard
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  delete --name "Core Metrics" --force
```

### Notes
- `--no-verify-ssl` disables TLS verification if you are using self-signed certs.
- Download uses a conservative cleanup by default to keep dashboards portable.
- Commands that accept `--name` support `--folder-id` to disambiguate duplicates.

## Grafana Dashboard Edit Utility
`grafana_dashboard_edit.py` manipulates panels inside a dashboard JSON file.
It expects a top-level `panels` list (grid layout dashboards).

### List panels
```bash
python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  list
```

### Duplicate a panel
```bash
python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  duplicate --id 12 --dy 8 --output dashboard_abcdEFGh.copy.json
```

### Move or resize a panel
```bash
python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  move --title "CPU Load" --x 0 --y 0 --w 12 --h 8 --in-place
```

### Swap two panels
```bash
python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  swap --a-id 3 --b-id 4 --output dashboard_abcdEFGh.swap.json
```

### Reflow and normalize ids
```bash
python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  reflow --padding 1 --in-place

python scripts/grafana_dashboard_edit.py \
  --input dashboard_abcdEFGh.json \
  normalize-ids --in-place
```

### Notes
- Write commands require `--output` or `--in-place`.
- The editor works with plain dashboard JSON or payloads that include a `dashboard` key.
- Row panels are supported; nested panels show a `rowPath` in JSON output.

## Grafana Infinity Data Source (fake)
`grafana_infinity_source.py` is a stdlib-only HTTP server that speaks the
row-based JSON shape expected by the Grafana Infinity datasource. It returns
deterministic fake time series and is meant for wiring up and testing Grafana
before a real backend is available.

### Run
```bash
python scripts/grafana_infinity_source.py --port 8080 \
  --metrics "temperature,pressure,flow" --step 10
```

### Endpoints
- `GET /health` → `{"status":"ok"}`
- `GET /metrics` → list of configured metric names (for variables)
- `GET /series?name=<m>&from=<ms>&to=<ms>&step=<s>` →
  `[{"time": <ms>, "value": <float>}, ...]`

`from`/`to` are Unix milliseconds (match Grafana's `${__from}` / `${__to}`).
`step` is in seconds. Defaults: last 1 h, step 10 s.

### Notes
- No external dependencies — stdlib only.
- Per-metric waveform is deterministic (seeded by name): sine + gaussian noise.
- Swap `generate_series` for a real backend when ready.

## List Virgo Channels
`list_virgo_channels.py` prints the channel names available in a virgotools
`FrameFile` source (e.g. `trend`, `raw`). Useful for populating
`channels-list.txt` or finding specific channels.

Requires the igwn conda environment:
```bash
source /cvmfs/software.igwn.org/conda/etc/profile.d/conda.sh
conda activate igwn
```

### Examples
```bash
# all ADC channels in 'trend' (default)
python scripts/list_virgo_channels.py > channels-list.txt

# counts per kind
python scripts/list_virgo_channels.py --kind all --count-only

# substring / prefix / regex filters (ANDed when combined)
python scripts/list_virgo_channels.py --contains DAQ --limit 20
python scripts/list_virgo_channels.py --prefix M1:DAQ_FbMain_dir
python scripts/list_virgo_channels.py --regex 'latency$'

# other sources / kinds
python scripts/list_virgo_channels.py --source raw --kind proc
```

### Flags
- `--source` — FrameFile source (default `trend`)
- `--kind {adc,proc,sms,sim,all}` — channel kind (default `adc`;
  `trend` only contains ADC)
- `--prefix`, `--contains`, `--regex` — filters
- `--limit N` — cap output rows
- `--count-only` — print only counts
- `--show-kind` — prefix lines with kind

### Notes
- Opening `trend` takes ~30–60 s the first time (large FFL).
- Diagnostics go to stderr so stdout stays pipe-friendly.
- Uses the undocumented-in-Python but stable `PyFd.FrFileIGet{Adc,Proc,Ser,Sim}Names`
  helpers; NULL returns (transient at frame boundaries on live FFLs) are
  retried once.

## Grafana Virgo Data Source (real)
`grafana_virgo_source.py` is the production counterpart of the fake source:
same URL shape, but the data comes from a virgotools `FrameFile`. Multiple
channels per request are supported and the response is pre-binned so Grafana
does not have to download millions of points.

Requires the igwn conda environment (virgotools, PyFd, astropy, numpy).

### Run
```bash
source /cvmfs/software.igwn.org/conda/etc/profile.d/conda.sh
conda activate igwn
python scripts/grafana_virgo_source.py --port 8080 \
  --source trend --channels-file channels-list.txt
```

### Endpoints
- `GET /health` → source name, `gps_start`, `gps_end`, channel count
- `GET /metrics?q=<substr>&limit=N` → filtered channel names from the
  whitelist file (7510+ entries, so filtering is recommended)
- `GET /series?names=<a>,<b>&from=<ms>&to=<ms>&step=<s>` →
  `[{"time": <ms>, "name": "<chan>", "value": <float>}, ...]`

`from`/`to` are Unix milliseconds (Grafana's `${__from}` / `${__to}`).
`step` is also in milliseconds; if omitted, ~1000 points are returned over the window.
Multiple channel names can be comma-separated; the response interleaves rows
from all of them and Grafana Infinity groups by `name` to render one series
per channel. Optionally `source` may also be set to specify per-request data source.

### Flags
- `--host` / `--port` (defaults `127.0.0.1:8080`)
- `--source` — virgotools FrameFile source (default `trend`)
- `--channels-file` — whitelist for `/metrics` and sanity check on `/series`
  (default `channels-list.txt`)
- `--max-points` — cap per channel per request (default `5000`, `0` disables);
  step auto-increases to stay under the cap

### Grafana Infinity configuration
- Type = Timeseries, Format = JSON, no root selector
- URL example: `http://<host>:8080/series?names=${chan}&from=${__from}&to=${__to}&step=${__interval_ms}`
- Columns: `time` (Time, unit ms), `name` (String), `value` (Number)
- Group by `name` to produce one series per channel

### Notes
- Requests are serialised via a lock around `getChannel` — the underlying C
  code is not documented as thread-safe.
- Rebinning is block-average (NaN-safe) when the requested `step_ms` is coarser
  than the channel's native sample period; otherwise samples are returned at
  the native rate.
