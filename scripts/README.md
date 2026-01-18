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
