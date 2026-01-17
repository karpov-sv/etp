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

### List tags
```bash
python scripts/grafana_dashboard.py \
  --base-url https://grafana.example.com \
  tags --query "cpu"
```

### Notes
- `--no-verify-ssl` disables TLS verification if you are using self-signed certs.
- Download uses a conservative cleanup by default to keep dashboards portable.
