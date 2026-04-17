#!/usr/bin/env python3
"""
cortex-exporter.py — Prometheus metrics exporter for Cortex
Runs as a persistent daemon (via @reboot cron or systemd).

Reads metrics written by cortex-monitor.py from a JSON state file
and exposes them as Prometheus-compatible text on a configurable port.

Dependencies: none beyond Python stdlib

Usage:
    python3 cortex-exporter.py
    python3 cortex-exporter.py --config /path/to/cortex.conf
    python3 cortex-exporter.py --port 9192
    python3 cortex-exporter.py --once    # print metrics to stdout and exit (for testing)
"""

import argparse
import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# ============================================================
#  Constants
# ============================================================

VERSION        = "1.0.0"
DEFAULT_CONFIG = "/opt/cortex/config/cortex.conf"
METRICS_FILE   = "/var/lib/cortex/metrics.json"
DIGEST_LOG     = "/var/lib/cortex/digest_history.json"
BUILD_INFO     = {"version": VERSION, "name": "cortex"}


# ============================================================
#  Config
# ============================================================

def load_config(path: str) -> dict:
    defaults = {
        "EXPORTER_PORT":  "9192",
        "EXPORTER_BIND":  "0.0.0.0",
        "LOG_LEVEL":      "INFO",
        "LOG_FILE":       "/var/log/cortex.log",
    }
    config = dict(defaults)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key   = key.strip()
                value = value.split("#")[0].strip()
                value = value.strip('"').strip("'")
                config[key] = value
    except FileNotFoundError:
        pass
    return config


# ============================================================
#  Metrics reading
# ============================================================

def load_metrics() -> dict:
    """Load current metrics from the JSON file written by cortex-monitor.py."""
    try:
        with open(METRICS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_digest_health() -> str:
    """Read the last system health value from digest history."""
    try:
        with open(DIGEST_LOG) as f:
            history = json.load(f)
        if history:
            return history[-1].get("health", "UNKNOWN")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return "UNKNOWN"


# ============================================================
#  Prometheus text format generation
# ============================================================

HEALTH_MAP = {"HEALTHY": 0, "DEGRADED": 1, "CRITICAL": 2, "UNKNOWN": -1}


def render_metrics() -> str:
    """
    Build the full Prometheus exposition format text.
    Each metric includes TYPE and HELP lines as per the specification.
    """
    m      = load_metrics()
    health = load_digest_health()
    now    = int(time.time())
    lines  = []

    def gauge(name: str, help_text: str, value, labels: dict = None) -> None:
        label_str = ""
        if labels:
            pairs     = ",".join(f'{k}="{v}"' for k, v in labels.items())
            label_str = f"{{{pairs}}}"
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{label_str} {value}")

    def counter(name: str, help_text: str, value, labels: dict = None) -> None:
        label_str = ""
        if labels:
            pairs     = ",".join(f'{k}="{v}"' for k, v in labels.items())
            label_str = f"{{{pairs}}}"
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name}{label_str} {value}")

    # --- Build info ---
    labels = {**BUILD_INFO, "goversion": f"python{sys.version_info.major}.{sys.version_info.minor}"}
    lines.append("# HELP cortex_build_info Cortex build information")
    lines.append("# TYPE cortex_build_info gauge")
    pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
    lines.append(f"cortex_build_info{{{pairs}}} 1")

    # --- Exporter health ---
    gauge("cortex_exporter_up",
          "Whether the Cortex exporter is running (always 1 if reachable)",
          1)

    gauge("cortex_exporter_scrape_timestamp",
          "Unix timestamp of when these metrics were last scraped",
          now)

    # --- Monitor run metrics ---
    last_run = m.get("cortex_last_run_timestamp", 0)
    gauge("cortex_last_run_timestamp",
          "Unix timestamp of the last cortex-monitor.py run",
          last_run)

    # Age of last run in seconds (useful for alerting on stale runs)
    age = now - last_run if last_run > 0 else -1
    gauge("cortex_last_run_age_seconds",
          "Seconds since the last cortex-monitor.py run",
          age)

    gauge("cortex_containers_monitored",
          "Number of Docker containers currently under Cortex monitoring",
          m.get("cortex_containers_monitored", 0))

    # --- Alert counters ---
    counter("cortex_alerts_total",
            "Total number of alert notifications sent by Cortex since startup",
            m.get("cortex_alerts_total", 0))

    counter("cortex_noise_filtered_total",
            "Total number of log lines suppressed by the noise filter",
            m.get("cortex_noise_filtered_total", 0))

    # --- Digest metrics ---
    last_digest = m.get("cortex_digest_last_sent", 0)
    gauge("cortex_digest_last_sent",
          "Unix timestamp of when the last daily digest was sent",
          last_digest)

    digest_age = now - last_digest if last_digest > 0 else -1
    gauge("cortex_digest_age_seconds",
          "Seconds since the last daily digest was sent",
          digest_age)

    # --- System health (from last digest) ---
    health_value = HEALTH_MAP.get(health, -1)
    gauge("cortex_system_health",
          "System health from last daily digest: 0=HEALTHY, 1=DEGRADED, 2=CRITICAL, -1=UNKNOWN",
          health_value,
          labels={"status": health})

    lines.append("")  # trailing newline required by Prometheus spec
    return "\n".join(lines)


# ============================================================
#  HTTP handler
# ============================================================

class MetricsHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/metrics", "/metrics/"):
            try:
                body    = render_metrics().encode("utf-8")
                status  = 200
                content = "text/plain; version=0.0.4; charset=utf-8"
            except Exception as e:
                body    = f"# ERROR generating metrics: {e}\n".encode("utf-8")
                status  = 500
                content = "text/plain"

            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path in ("/health", "/healthz"):
            body = b"OK\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/":
            body = (
                b"<html><head><title>Cortex Exporter</title></head>"
                b"<body><h1>Cortex Prometheus Exporter</h1>"
                b'<p><a href="/metrics">Metrics</a></p>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # Suppress default access log to avoid flooding cortex.log
        # Uncomment below to re-enable access logging
        # logging.getLogger("cortex-exporter").debug(fmt % args)
        pass


# ============================================================
#  Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Cortex Prometheus metrics exporter")
    parser.add_argument("--config",  default=DEFAULT_CONFIG, help="Path to cortex.conf")
    parser.add_argument("--port",    type=int, default=None,  help="Override EXPORTER_PORT from config")
    parser.add_argument("--once",    action="store_true",     help="Print metrics to stdout and exit")
    parser.add_argument("--version", action="store_true",     help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"Cortex exporter v{VERSION}")
        sys.exit(0)

    config = load_config(args.config)

    # Logging
    level = getattr(logging, config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] cortex-exporter: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("cortex-exporter")

    # --once mode: dump metrics and exit
    if args.once:
        print(render_metrics())
        sys.exit(0)

    # Determine bind address and port
    port = args.port or int(config.get("EXPORTER_PORT", 9192))
    bind = config.get("EXPORTER_BIND", "0.0.0.0")

    logger.info(f"Cortex exporter v{VERSION} listening on {bind}:{port}")
    logger.info(f"Metrics endpoint: http://{bind}:{port}/metrics")

    # Validate that the metrics file directory exists
    Path(METRICS_FILE).parent.mkdir(parents=True, exist_ok=True)

    server = HTTPServer((bind, port), MetricsHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Exporter stopped by user")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
