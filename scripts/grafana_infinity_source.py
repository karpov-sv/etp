"""Infinity-style HTTP data source for Grafana.

Serves fake time series so the Grafana Infinity datasource can be wired up and
visualised before a real backend exists. Swap `generate_series` for an actual
data access layer later.

Endpoints
---------
GET /health                                    -> {"status":"ok"}
GET /metrics                                   -> ["temp", "pressure", ...]
GET /series?name=<m>&from=<ms>&to=<ms>&step=<s> -> [{"time": <ms>, "value": <f>}, ...]

`from` / `to` are Unix milliseconds (matches Grafana's ${__from} / ${__to}).
`step` is in seconds. Defaults: last 1 hour, step 10 s.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


DEFAULT_METRICS = ("temperature", "pressure", "flow")
DEFAULT_WINDOW_S = 3600
DEFAULT_STEP_S = 10


def _seed(name: str) -> int:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def generate_series(name, t0_ms, t1_ms, step_s):
    """Return a list of {time, value} points for `name` in [t0_ms, t1_ms].

    Deterministic per metric name: each name gets its own base, amplitude,
    period, and noise level derived from a hash of the name.
    """
    if step_s <= 0:
        raise ValueError("step_s must be positive")
    if t1_ms < t0_ms:
        t0_ms, t1_ms = t1_ms, t0_ms

    rng = random.Random(_seed(name))
    base = rng.uniform(10.0, 100.0)
    amplitude = rng.uniform(1.0, 20.0)
    period_s = rng.uniform(120.0, 1800.0)
    noise = rng.uniform(0.05, 0.5) * amplitude
    phase = rng.uniform(0.0, 2 * math.pi)

    step_ms = int(step_s * 1000)
    t = (t0_ms // step_ms) * step_ms
    points = []
    while t <= t1_ms:
        x = (t / 1000.0) / period_s * 2 * math.pi + phase
        value = base + amplitude * math.sin(x) + rng.gauss(0.0, noise)
        points.append({"time": t, "value": round(value, 4)})
        t += step_ms
    return points


class InfinityHandler(BaseHTTPRequestHandler):
    server_version = "InfinityFake/0.1"

    def log_message(self, fmt, *args):
        # Quieter default logging; prefix with timestamp.
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json(status, {"error": message})

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/metrics":
            self._send_json(200, list(self.server.metrics))
            return

        if path == "/series":
            self._handle_series(query)
            return

        self._send_error_json(404, f"unknown path: {path}")

    def _handle_series(self, query):
        name = (query.get("name") or [None])[0]
        if not name:
            self._send_error_json(400, "missing required query parameter: name")
            return
        if name not in self.server.metrics:
            self._send_error_json(404, f"unknown metric: {name}")
            return

        now_ms = int(time.time() * 1000)
        default_from = now_ms - self.server.default_window_s * 1000

        try:
            t0 = int((query.get("from") or [default_from])[0])
            t1 = int((query.get("to") or [now_ms])[0])
            step_s = float((query.get("step") or [self.server.default_step_s])[0])
        except (TypeError, ValueError) as exc:
            self._send_error_json(400, f"invalid numeric parameter: {exc}")
            return

        try:
            points = generate_series(name, t0, t1, step_s)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return

        self._send_json(200, points)


class InfinityServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, metrics, default_window_s, default_step_s):
        super().__init__(address, InfinityHandler)
        self.metrics = tuple(metrics)
        self.default_window_s = default_window_s
        self.default_step_s = default_step_s


def _parse_metrics(raw):
    items = [m.strip() for m in raw.split(",")]
    items = [m for m in items if m]
    if not items:
        raise argparse.ArgumentTypeError("metrics list cannot be empty")
    return items


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--metrics",
        type=_parse_metrics,
        default=list(DEFAULT_METRICS),
        help="comma-separated metric names (default: %(default)s)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW_S,
        help="default time window in seconds when 'from' is omitted",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=DEFAULT_STEP_S,
        help="default step between points in seconds",
    )
    args = parser.parse_args(argv)

    server = InfinityServer(
        (args.host, args.port),
        metrics=args.metrics,
        default_window_s=args.window,
        default_step_s=args.step,
    )
    print(f"Infinity fake source listening on http://{args.host}:{args.port}")
    print(f"Metrics: {', '.join(args.metrics)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
