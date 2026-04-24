"""Grafana Infinity data source backed by virgotools.

Serves time series pulled from a virgotools FrameFile (default source: 'trend').
Designed to be queried by the Grafana Infinity datasource with an API-style URL:

    /series?names=M1:a,M1:b&from=<unix_ms>&to=<unix_ms>&step_ms=<ms>

Response is a list of rows:

    [{"time": <unix_ms>, "name": "M1:a", "value": <float>}, ...]

Configure Infinity with columns: time (Time/Timestamp ms), name (String),
value (Number). Group by name to get one series per channel.

Requires the igwn conda environment (virgotools, PyFd, astropy, numpy).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


try:
    import numpy as np
    from astropy.time import Time
    from virgotools import FrameFile, getChannel
    from virgotools.frame_lib import ChannelNotFound
except ImportError as exc:
    sys.exit(
        f"error: {exc}\n"
        "This script needs the igwn conda env:\n"
        "  source /cvmfs/software.igwn.org/conda/etc/profile.d/conda.sh\n"
        "  conda activate igwn"
    )


def _load_channels_file(path):
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def unix_ms_to_gps(ms):
    return float(Time(ms / 1000.0, format="unix").gps)


def rebin(values, native_dt, gps_start, step_s):
    """Block-average `values` into bins of `step_s` seconds.

    Returns (timestamps_ms, averaged_values). If step_s <= native_dt, returns
    the samples at their native rate instead.
    """
    n = values.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)

    # GPS epoch is 1980-01-06 00:00:00 UTC. Leap seconds separate GPS from UTC.
    # Converting per-sample via astropy is slow; do it once on the endpoint and
    # apply the same offset to every sample within a response (safe: leap seconds
    # can't be inserted inside a single request's time window for trend data).
    gps_offset = float(Time(gps_start, format="gps").unix) - gps_start

    if step_s <= native_dt:
        t_gps = gps_start + np.arange(n, dtype=np.float64) * native_dt
        return (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False), values.astype(np.float64, copy=False)

    samples_per_bin = max(1, int(round(step_s / native_dt)))
    trimmed = n - (n % samples_per_bin)
    if trimmed == 0:
        # not even one full bin — return a single-point bin of the mean
        bin_means = np.array([np.nanmean(values)], dtype=np.float64)
        t_gps = np.array([gps_start + (n * native_dt) / 2.0], dtype=np.float64)
        return (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False), bin_means

    reshaped = values[:trimmed].reshape(-1, samples_per_bin).astype(np.float64, copy=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        bin_means = np.nanmean(reshaped, axis=1)

    bin_dt = samples_per_bin * native_dt
    t_gps = gps_start + (np.arange(bin_means.shape[0], dtype=np.float64) + 0.5) * bin_dt
    return (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False), bin_means


class VirgoSource:
    def __init__(self, source_name, channels_file, max_points):
        print(f"[{time.strftime('%H:%M:%S')}] Will serve data from {source_name!r}", flush=True)
        self.source_name = source_name
        self.channels = _load_channels_file(channels_file)
        self._channel_set = set(self.channels)
        self.max_points = max_points
        self._lock = threading.Lock()

    def fetch_rows(self, name, gps_start, gps_end, step_s):
        dur = max(gps_end - gps_start, 0.0)
        if dur == 0:
            return []

        # Serialise: the underlying C code is not documented as thread-safe.
        with self._lock:
            try:
                vect = getChannel(self.source_name, name, gps_start, dur)
            except ChannelNotFound:
                raise

        native_dt = float(vect.dt)
        v_gps = float(vect.gps)
        # Enforce the max_points cap by bumping step_s up if needed.
        if self.max_points > 0:
            n_samples = vect.data.shape[0]
            effective_step = max(step_s, native_dt)
            projected = int(math.ceil(dur / effective_step))
            if projected > self.max_points:
                effective_step = dur / self.max_points
                step_s = effective_step
        t_ms, values = rebin(vect.data, native_dt, v_gps, step_s)
        rows = []
        for t, v in zip(t_ms, values):
            if np.isnan(v):
                continue
            rows.append({"time": int(t), "name": name, "value": float(v)})
        return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "VirgoInfinity/0.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self.log_message("Error: %s", message)
        self._send_json(status, {"error": message})

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        src = self.server.source

        if path == "/health":
            ff = FrameFile(src.source_name)
            self._send_json(200, {
                "status": "ok",
                "source": src.source_name,
                "gps_start": ff.gps_start,
                "gps_end": ff.gps_end,
                "channels": len(src.channels),
            })
            return

        if path == "/metrics":
            q = (query.get("q") or [""])[0]
            try:
                limit = int((query.get("limit") or ["0"])[0])
            except ValueError:
                limit = 0
            names = src.channels
            if q:
                names = [n for n in names if q in n]
            if limit > 0:
                names = names[:limit]
            self._send_json(200, names)
            return

        if path == "/series":
            self._handle_series(query, src)
            return

        self._error(404, f"unknown path: {path}")

    def _handle_series(self, query, src):
        raw_names = query.get("names") or query.get("name")
        if not raw_names:
            self._error(400, "missing required query parameter: names")
            return
        names = []
        for item in raw_names:
            names.extend(n.strip() for n in item.split(",") if n.strip())
        if not names:
            self._error(400, "no channel names provided")
            return

        unknown = [n for n in names if src._channel_set and n not in src._channel_set]
        if unknown:
            self._error(404, f"unknown channel(s): {','.join(unknown)}")
            return

        try:
            t1_ms = int((query.get("to") or [int(time.time() * 1000)])[0])
            default_from = t1_ms - 3600_000
            t0_ms = int((query.get("from") or [default_from])[0])
            step_s = float((query.get("step_ms") or ["0"])[0])/1000
        except (TypeError, ValueError) as exc:
            self._error(400, f"invalid numeric parameter: {exc}")
            return

        if t1_ms <= t0_ms:
            self._error(400, "'to' must be greater than 'from'")
            return

        gps_start = unix_ms_to_gps(t0_ms)
        gps_end = unix_ms_to_gps(t1_ms)
        # If step not given, target ~1000 points over the window.
        if step_s <= 0:
            step_s = max((gps_end - gps_start) / 1000.0, 0.001)

        rows = []
        for name in names:
            try:
                rows.extend(src.fetch_rows(name, gps_start, gps_end, step_s))
            except ChannelNotFound:
                self._error(404, f"channel not found in frames: {name}")
                return

        self._send_json(200, rows)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, source):
        super().__init__(address, Handler)
        self.source = source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--source", default="trend",
                        help="virgotools FrameFile source (default: trend)")
    parser.add_argument("--channels-file", default="channels-list.txt",
                        help="one-channel-per-line whitelist; used for /metrics "
                             "and as a sanity check on /series names")
    parser.add_argument("--max-points", type=int, default=5000,
                        help="cap on points returned per channel per request "
                             "(0 disables the cap)")
    args = parser.parse_args(argv)

    source = VirgoSource(args.source, args.channels_file, args.max_points)
    server = Server((args.host, args.port), source)
    print(f"Virgo Infinity source listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
