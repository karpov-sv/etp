# Devices

## Dummy Device
`dummy.py` is a reference device daemon that emits synthetic readings and ingests
them into InfluxDB. It exposes a simple command interface over TCP.

### Requirements
- Install optional dependencies: `python -m pip install -e '.[influx]'`
- Provide InfluxDB settings in a `.env` file (loaded via `python-decouple`)

Required `.env` keys:
- `INFLUX_VERSION`: `v2` or `v3` (default `v2`)
- `INFLUX_BASE_URL`
- `INFLUX_TOKEN`
- `INFLUX_ORG`, `INFLUX_BUCKET`, `INFLUX_PRECISION` (v2 only; precision default: `ns`)
- `INFLUX_DB` (v3 only)

### Run
```bash
python devices/dummy.py --host 127.0.0.1 --port 7004 --rate 1.0 \
  --metric-name temperature --tag site=lab --tag rack=3 --debug
```

### Commands
Connect with `nc` or `client.py` and send newline or NUL terminated commands.
- `status` returns the current state snapshot.
- `set key=value` updates shared state (e.g., `set rate=0.5`).
- `exit` stops the daemon quickly (skips Influx drain).

### Notes
- `rate` is in readings per second and can be changed at runtime with `set rate=...`.
- `--metric-name` (alias `--parameter-name`) controls the status key and Influx measurement name.
- `--tag` can be repeated to attach additional Influx tags to each point.
- `--debug` enables verbose logging from the daemon and Influx writer.
