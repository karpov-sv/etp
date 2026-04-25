"""Grafana Infinity data source backed by virgotools.

Serves time series pulled from a virgotools FrameFile (default source: 'trend').
Designed to be queried by the Grafana Infinity datasource with an API-style URL:

    /series?names=M1:a,M1:b&from=<unix_ms>&to=<unix_ms>&step=<ms>&source=<name>

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_channels_file(path):
    """Read a whitelist of channel names, one per line. Lines starting with
    '#' and blank lines are ignored. Returns [] if the file is missing."""
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def unix_ms_to_gps(ms):
    """Convert a Unix timestamp in milliseconds to GPS seconds."""
    return float(Time(ms / 1000.0, format="unix").gps)


def _qs_first(query, key, default=None):
    """Return the first value for `key` in a parsed query string, or `default`."""
    values = query.get(key)
    if not values:
        return default
    return values[0]


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

    # Native-rate fast path: caller asked for finer or equal resolution than
    # the data already has, so just emit each sample as-is.
    if step_s <= native_dt:
        t_gps = gps_start + np.arange(n, dtype=np.float64) * native_dt
        t_ms = (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False)
        return t_ms, values.astype(np.float64, copy=False)

    # How many native samples make up one output bin.
    samples_per_bin = max(1, int(round(step_s / native_dt)))
    trimmed = n - (n % samples_per_bin)

    # Window is shorter than a single bin: fold everything into one point.
    if trimmed == 0:
        bin_means = np.array([np.nanmean(values)], dtype=np.float64)
        t_gps = np.array([gps_start + (n * native_dt) / 2.0], dtype=np.float64)
        t_ms = (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False)
        return t_ms, bin_means

    # Block-average: reshape into (n_bins, samples_per_bin) and take row means.
    reshaped = values[:trimmed].reshape(-1, samples_per_bin).astype(np.float64, copy=False)
    with warnings.catch_warnings():
        # nanmean of an all-NaN bin warns; we'd rather just get NaN back.
        warnings.simplefilter("ignore", category=RuntimeWarning)
        bin_means = np.nanmean(reshaped, axis=1)

    # Place each bin's timestamp at the centre of the bin.
    bin_dt = samples_per_bin * native_dt
    t_gps = gps_start + (np.arange(bin_means.shape[0], dtype=np.float64) + 0.5) * bin_dt
    t_ms = (1000 * (t_gps + gps_offset)).astype(np.int64, copy=False)
    return t_ms, bin_means


# ---------------------------------------------------------------------------
# Data source
# ---------------------------------------------------------------------------

class VirgoSource:
    """Wraps virgotools.getChannel with a channel whitelist and a downsampling
    cap so a single HTTP request can't pull megabytes of samples."""

    def __init__(self, source_name, channels_file, max_points):
        print(f"[{time.strftime('%H:%M:%S')}] Will serve data from {source_name!r}", flush=True)
        self.source_name = source_name
        self.channels = _load_channels_file(channels_file)
        self._channel_set = set(self.channels)
        self.max_points = max_points
        # virgotools' underlying C code is not documented as thread-safe, so
        # we serialise all getChannel() calls behind this lock.
        self._lock = threading.Lock()

    def fetch_rows(self, name, gps_start, gps_end, step_s, source_name=None):
        """Fetch one channel and return Infinity-style rows. `source_name`
        overrides the default FrameFile source for this call only."""
        dur = max(gps_end - gps_start, 0.0)
        if dur == 0:
            return []

        src_name = source_name or self.source_name
        with self._lock:
            vect = getChannel(src_name, name, gps_start, dur)

        native_dt = float(vect.dt)
        v_gps = float(vect.gps)

        # Enforce the max_points cap by widening step_s if the request would
        # otherwise return more points than allowed.
        if self.max_points > 0:
            effective_step = max(step_s, native_dt)
            projected = int(math.ceil(dur / effective_step))
            if projected > self.max_points:
                step_s = dur / self.max_points

        t_ms, values = rebin(vect.data, native_dt, v_gps, step_s)

        # Drop NaN bins (gaps in data); Grafana plots cleanly without them.
        rows = []
        for t, v in zip(t_ms, values):
            if np.isnan(v):
                continue
            rows.append({"time": int(t), "name": name, "value": float(v)})
        return rows


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

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
            self._handle_health(src)
        elif path == "/metrics":
            self._handle_metrics(query, src)
        elif path == "/series":
            self._handle_series(query, src)
        else:
            self._error(404, f"unknown path: {path}")

    def _handle_health(self, src):
        ff = FrameFile(src.source_name)
        self._send_json(200, {
            "status": "ok",
            "source": src.source_name,
            "gps_start": ff.gps_start,
            "gps_end": ff.gps_end,
            "channels": len(src.channels),
        })

    def _handle_metrics(self, query, src):
        """Return the channel whitelist, optionally filtered by substring."""
        q = _qs_first(query, "q", "")
        try:
            limit = int(_qs_first(query, "limit", "0"))
        except ValueError:
            limit = 0

        names = src.channels
        if q:
            names = [n for n in names if q in n]
        if limit > 0:
            names = names[:limit]
        self._send_json(200, names)

    def _handle_series(self, query, src):
        # 1. Parse channel names (comma-separated, repeatable).
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

        # Whitelist check is currently disabled because the channel list
        # depends on which `source` is being queried, and we only load one.
        # unknown = [n for n in names if src._channel_set and n not in src._channel_set]
        # if unknown:
        #     self._error(404, f"unknown channel(s): {','.join(unknown)}")
        #     return

        # 2. Parse the time window and step. Defaults: last hour, auto-step.
        try:
            now_ms = int(time.time() * 1000)
            t1_ms = int(_qs_first(query, "to", now_ms))
            t0_ms = int(_qs_first(query, "from", t1_ms - 3600_000))
            step_s = float(_qs_first(query, "step", "0")) / 1000
        except (TypeError, ValueError) as exc:
            self._error(400, f"invalid numeric parameter: {exc}")
            return

        if t1_ms <= t0_ms:
            self._error(400, "'to' must be greater than 'from'")
            return

        # 3. Optional per-request override of the FrameFile source name.
        source_override = _qs_first(query, "source")
        if source_override is not None:
            source_override = source_override.strip() or None

        gps_start = unix_ms_to_gps(t0_ms)
        gps_end = unix_ms_to_gps(t1_ms)

        # If step was not specified, target ~1000 points across the window.
        if step_s <= 0:
            step_s = max((gps_end - gps_start) / 1000.0, 0.001)

        # 4. Fetch each requested channel and concatenate the rows.
        rows = []
        for name in names:
            try:
                rows.extend(src.fetch_rows(name, gps_start, gps_end, step_s, source_override))
            except ChannelNotFound:
                self._error(404, f"channel not found in frames: {name}")
                return

        self._send_json(200, rows)


# ---------------------------------------------------------------------------
# Server / entry point
# ---------------------------------------------------------------------------

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
                        help="default virgotools FrameFile source; can be "
                             "overridden per-request via ?source=")
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
