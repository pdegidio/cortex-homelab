#!/usr/bin/env python3
"""
cortex-digest.py — Daily digest generator for Cortex
Runs once per day via cron (recommended: 09:00).

Reads the state file built up by cortex-monitor.py throughout the day,
sends the full log output through the LLM for a narrative daily summary,
and pushes the result via ntfy.

Dependencies:
    pip install requests

Usage:
    python3 cortex-digest.py
    python3 cortex-digest.py --config /path/to/cortex.conf
    python3 cortex-digest.py --dry-run   # generate digest but do not send
    python3 cortex-digest.py --since 24  # hours to look back (default: 24)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests")
    sys.exit(1)


# ============================================================
#  Constants
# ============================================================

VERSION        = "1.0.0"
DEFAULT_CONFIG = "/opt/cortex/config/cortex.conf"
DEFAULT_STATE  = "/var/lib/cortex/state.json"
METRICS_FILE   = "/var/lib/cortex/metrics.json"
DIGEST_LOG     = "/var/lib/cortex/digest_history.json"


# ============================================================
#  Config (re-used from monitor — same format)
# ============================================================

def load_config(path: str) -> dict:
    defaults = {
        "OLLAMA_HOST":            "http://localhost:11434",
        "OLLAMA_MODEL":           "cortex:latest",
        "OLLAMA_TIMEOUT":         "90",
        "NTFY_URL":               "",
        "NTFY_TOPIC":             "homelab-system",
        "NTFY_TOKEN":             "",
        "MONITORED_CONTAINERS":   "",
        "DOCKER_SOCKET":          "/var/run/docker.sock",
        "LOG_LINES":              "200",
        "NOISE_PATTERNS":         "ffprobe,VideoFileInfoReader,429,invalid torrent,9696/",
        "STATE_FILE":             DEFAULT_STATE,
        "LOG_LEVEL":              "INFO",
        "LOG_FILE":               "/var/log/cortex-digest.log",
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
        print(f"WARNING: Config file not found at {path}, using defaults.")
    return config


# ============================================================
#  Logging
# ============================================================

def setup_logging(config: dict) -> logging.Logger:
    level    = getattr(logging, config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_file = config.get("LOG_FILE", "/var/log/cortex-digest.log")
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file))
    except PermissionError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("cortex-digest")


# ============================================================
#  Data collection for digest
# ============================================================

def get_container_logs(name: str, since_hours: int) -> str:
    """Fetch logs for the past N hours from a container."""
    since = f"{since_hours}h"
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", since, "--timestamps", name],
            capture_output=True, text=True, timeout=20
        )
        output = result.stderr if result.stderr else result.stdout
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"[ERROR] Log fetch timed out for: {name}"
    except Exception as e:
        return f"[ERROR] {name}: {e}"


def get_container_status(name: str) -> str:
    """Return current status string for a container."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else "not found"
    except Exception:
        return "unknown"


def filter_noise(text: str, patterns: list) -> tuple:
    if not text or not patterns:
        return text, 0
    lines = text.splitlines()
    filtered, suppressed = [], 0
    for line in lines:
        if any(p.lower() in line.lower() for p in patterns if p.strip()):
            suppressed += 1
        else:
            filtered.append(line)
    return "\n".join(filtered), suppressed


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerts": {}, "last_run": None, "run_count": 0}


def load_metrics() -> dict:
    try:
        with open(METRICS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def count_alerts_today(state: dict) -> dict:
    """Count how many alerts were sent today by severity."""
    today = datetime.now().date()
    counts = {"critical": 0, "warnings": 0}
    for key, timestamp in state.get("alerts", {}).items():
        try:
            if datetime.fromisoformat(timestamp).date() == today:
                if "critical" in key:
                    counts["critical"] += 1
                elif "warnings" in key:
                    counts["warnings"] += 1
        except (ValueError, TypeError):
            pass
    return counts


def collect_digest_data(config: dict, since_hours: int, logger: logging.Logger) -> dict:
    """
    Collect all data needed for the daily digest:
    - Current status of all containers
    - Logs from the past N hours
    - Alert history from state file
    - System metrics
    """
    container_names = config.get("MONITORED_CONTAINERS", "").split()
    noise_patterns  = [p.strip() for p in config.get("NOISE_PATTERNS", "").split(",")]
    state           = load_state(config.get("STATE_FILE", DEFAULT_STATE))
    metrics         = load_metrics()

    container_data = []
    total_suppressed = 0

    for name in container_names:
        if not name:
            continue
        logger.debug(f"Collecting digest data for: {name}")
        status     = get_container_status(name)
        raw_logs   = get_container_logs(name, since_hours)
        clean_logs, suppressed = filter_noise(raw_logs, noise_patterns)
        total_suppressed += suppressed

        container_data.append({
            "name":      name,
            "status":    status,
            "log_lines": len(raw_logs.splitlines()),
            "logs":      clean_logs,
        })

    return {
        "containers":        container_data,
        "alert_counts":      count_alerts_today(state),
        "metrics":           metrics,
        "total_suppressed":  total_suppressed,
        "state_run_count":   state.get("run_count", 0),
        "last_monitor_run":  state.get("last_run", "unknown"),
    }


# ============================================================
#  LLM digest generation
# ============================================================

def build_digest_prompt(data: dict, since_hours: int) -> str:
    today     = datetime.now().strftime("%Y-%m-%d")
    now       = datetime.now().strftime("%H:%M")
    metrics   = data["metrics"]
    alerts    = data["alert_counts"]

    # Status summary header
    status_lines = []
    for c in data["containers"]:
        emoji = "✅" if c["status"] == "running" else "❌"
        status_lines.append(f"  {emoji} {c['name']}: {c['status']} ({c['log_lines']} log lines collected)")
    status_block = "\n".join(status_lines) if status_lines else "  (no containers configured)"

    # Logs block — truncate per container to avoid hitting context limit
    MAX_LOG_CHARS = 1500
    log_blocks = []
    for c in data["containers"]:
        logs = c["logs"]
        if len(logs) > MAX_LOG_CHARS:
            # Keep tail — most recent events are more relevant
            logs = "[... truncated ...]\n" + logs[-MAX_LOG_CHARS:]
        log_blocks.append(
            f"=== {c['name'].upper()} | {c['status'].upper()} ===\n"
            f"{logs if logs else '[no log output after filtering]'}"
        )
    logs_section = "\n\n".join(log_blocks)

    # Metrics context
    total_alerts    = metrics.get("cortex_alerts_total", 0)
    noise_total     = metrics.get("cortex_noise_filtered_total", 0)
    containers_live = metrics.get("cortex_containers_monitored", len(data["containers"]))

    return f"""Generate a Cortex DAILY DIGEST for {today}.

## MONITORING SUMMARY (last {since_hours} hours)

Monitor runs completed today: {data['state_run_count']}
Last run: {data['last_monitor_run']}
Alerts sent today — critical: {alerts['critical']}, warnings: {alerts['warnings']}
Total alerts sent (all time): {total_alerts}
Noise lines suppressed today: {data['total_suppressed']}
Containers monitored: {containers_live}

## CURRENT CONTAINER STATUS

{status_block}

## LOG DATA (last {since_hours} hours, noise filtered)

{logs_section}

---
Produce the CORTEX DAILY DIGEST in the exact format specified in your instructions.
Date: {today}. Current time: {now}.
Do not add any text outside the digest format.
"""


def query_ollama(config: dict, prompt: str, logger: logging.Logger) -> str | None:
    host    = config.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model   = config.get("OLLAMA_MODEL", "cortex:latest")
    timeout = int(config.get("OLLAMA_TIMEOUT", 90))
    url     = f"{host}/api/generate"

    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }

    try:
        logger.info(f"Querying Ollama for digest (model: {model}, timeout: {timeout}s)...")
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to Ollama at {host}")
    except requests.exceptions.Timeout:
        logger.error(f"Ollama timed out after {timeout}s — consider increasing OLLAMA_TIMEOUT for digest")
    except Exception as e:
        logger.error(f"Ollama error: {e}")
    return None


# ============================================================
#  Fallback digest (when LLM is unavailable)
# ============================================================

def build_fallback_digest(data: dict) -> str:
    today  = datetime.now().strftime("%Y-%m-%d")
    alerts = data["alert_counts"]

    lines  = [f"CORTEX DAILY DIGEST — {today}", ""]
    all_running = all(c["status"] == "running" for c in data["containers"])
    health = "HEALTHY" if all_running and alerts["critical"] == 0 else "DEGRADED"
    lines.append(f"SYSTEM HEALTH: {health}")
    lines.append("")
    lines.append("NOTE: LLM was unavailable — this is a raw status digest.")
    lines.append("")
    lines.append("CONTAINER STATUS:")
    for c in data["containers"]:
        icon = "OK" if c["status"] == "running" else "DOWN"
        lines.append(f"  [{icon}] {c['name']}: {c['status']}")
    lines.append("")
    lines.append(f"Alerts today — critical: {alerts['critical']}, warnings: {alerts['warnings']}")

    return "\n".join(lines)


# ============================================================
#  ntfy delivery
# ============================================================

def send_ntfy(config: dict, title: str, message: str,
              priority: str = "default", logger: logging.Logger = None) -> bool:
    base_url = config.get("NTFY_URL", "").rstrip("/")
    if not base_url:
        if logger:
            logger.warning("NTFY_URL not configured — skipping digest notification")
        return False

    topic   = config.get("NTFY_TOPIC", "homelab-system")
    url     = f"{base_url}/{topic}"
    headers = {
        "Title":    title,
        "Priority": priority,
        "Tags":     "spiral_notepad,cortex",
    }
    token = config.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # ntfy has a message size limit — truncate gracefully
    MAX_BODY = 4096
    if len(message) > MAX_BODY:
        message = message[:MAX_BODY - 50] + "\n\n[... truncated — see logs for full digest]"

    try:
        resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        if logger:
            logger.info(f"Digest sent via ntfy: {title}")
        return True
    except Exception as e:
        if logger:
            logger.error(f"Failed to send digest via ntfy: {e}")
        return False


# ============================================================
#  Digest history
# ============================================================

def save_digest_history(digest_text: str, health: str) -> None:
    """Append this digest to a rolling history file (keeps last 30 days)."""
    Path(DIGEST_LOG).parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(DIGEST_LOG) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    history.append({
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "time":   datetime.now().strftime("%H:%M"),
        "health": health,
        "digest": digest_text,
    })

    # Keep last 30 entries
    history = history[-30:]

    with open(DIGEST_LOG, "w") as f:
        json.dump(history, f, indent=2)


def update_digest_metric() -> None:
    """Update the last digest timestamp in the metrics file."""
    try:
        with open(METRICS_FILE) as f:
            metrics = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        metrics = {}

    metrics["cortex_digest_last_sent"] = int(time.time())

    Path(METRICS_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_FILE, "w") as f:
        json.dump(metrics, f, indent=2)


# ============================================================
#  Health inference from digest text
# ============================================================

def infer_health(digest_text: str) -> str:
    """Extract SYSTEM HEALTH value from digest text."""
    for line in digest_text.splitlines():
        if line.strip().startswith("SYSTEM HEALTH:"):
            value = line.split(":", 1)[1].strip().upper()
            if value in ("HEALTHY", "DEGRADED", "CRITICAL"):
                return value
    return "UNKNOWN"


# ============================================================
#  Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Cortex daily digest generator")
    parser.add_argument("--config",  default=DEFAULT_CONFIG, help="Path to cortex.conf")
    parser.add_argument("--dry-run", action="store_true",    help="Generate digest but do not send")
    parser.add_argument("--since",   type=int, default=24,   help="Hours to look back (default: 24)")
    parser.add_argument("--version", action="store_true",    help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"Cortex digest v{VERSION}")
        sys.exit(0)

    config = load_config(args.config)
    logger = setup_logging(config)
    logger.info(f"Cortex digest v{VERSION} starting (since={args.since}h, dry_run={args.dry_run})")

    # Collect
    logger.info(f"Collecting data for the past {args.since} hours...")
    data = collect_digest_data(config, args.since, logger)
    logger.info(
        f"Data collected: {len(data['containers'])} containers, "
        f"{data['total_suppressed']} lines suppressed"
    )

    # Generate digest
    prompt       = build_digest_prompt(data, args.since)
    digest_text  = query_ollama(config, prompt, logger)
    used_llm     = True

    if digest_text is None:
        logger.warning("LLM unavailable — falling back to raw status digest")
        digest_text = build_fallback_digest(data)
        used_llm    = False

    logger.debug(f"Digest:\n{digest_text}")

    # Determine health and build ntfy title
    health = infer_health(digest_text)
    health_emoji = {"HEALTHY": "🟢", "DEGRADED": "🟡", "CRITICAL": "🔴"}.get(health, "⚪")
    title = f"{health_emoji} Cortex Daily — {datetime.now().strftime('%Y-%m-%d')} — {health}"
    if not used_llm:
        title += " (LLM offline)"

    # Send
    if args.dry_run:
        logger.info(f"[DRY RUN] Would send digest: {title}")
        print("\n" + "=" * 60)
        print(digest_text)
        print("=" * 60)
    else:
        priority = "default" if health == "HEALTHY" else "high"
        send_ntfy(config, title, digest_text, priority=priority, logger=logger)

    # Persist
    save_digest_history(digest_text, health)
    update_digest_metric()

    logger.info(f"Digest complete — system health: {health}")


if __name__ == "__main__":
    main()
