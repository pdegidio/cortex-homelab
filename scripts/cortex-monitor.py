#!/usr/bin/env python3
"""
cortex-monitor.py — Core monitoring script for Cortex
Runs every 30 minutes via cron.

Dependencies:
    pip install requests

Usage:
    python3 cortex-monitor.py
    python3 cortex-monitor.py --config /path/to/cortex.conf
    python3 cortex-monitor.py --dry-run   # analyse logs but do not send alerts
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

VERSION = "1.0.0"
DEFAULT_CONFIG = "/opt/cortex/config/cortex.conf"
DEFAULT_STATE  = "/var/lib/cortex/state.json"
METRICS_FILE   = "/var/lib/cortex/metrics.json"


# ============================================================
#  Configuration
# ============================================================

def load_config(path: str) -> dict:
    """
    Parse a simple key=value config file.
    Lines starting with # are ignored. Inline comments are stripped.
    """
    defaults = {
        "OLLAMA_HOST":             "http://localhost:11434",
        "OLLAMA_MODEL":            "cortex:latest",
        "OLLAMA_TIMEOUT":          "60",
        "NTFY_URL":                "",
        "NTFY_TOPIC":              "homelab-system",
        "NTFY_TOPIC_CRITICAL":     "",
        "NTFY_TOKEN":              "",
        "MONITORED_CONTAINERS":    "",
        "DOCKER_SOCKET":           "/var/run/docker.sock",
        "LOG_LINES":               "100",
        "SONARR_URL":              "",
        "SONARR_API_KEY":          "",
        "RADARR_URL":              "",
        "RADARR_API_KEY":          "",
        "PROWLARR_URL":            "",
        "PROWLARR_API_KEY":        "",
        "NOISE_PATTERNS":          "ffprobe,VideoFileInfoReader,429,invalid torrent,9696/",
        "NOISE_DEBUG":             "false",
        "STATE_FILE":              DEFAULT_STATE,
        "ALERT_COOLDOWN_MINUTES":  "120",
        "DIGEST_HOUR":             "9",
        "EXPORTER_PORT":           "9192",
        "LOG_LEVEL":               "INFO",
        "LOG_FILE":                "/var/log/cortex.log",
        "LOG_MAX_SIZE_MB":         "10",
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
                value = value.split("#")[0].strip()   # strip inline comments
                value = value.strip('"').strip("'")   # strip optional quotes
                config[key] = value
    except FileNotFoundError:
        print(f"WARNING: Config file not found at {path}, using defaults.")

    return config


# ============================================================
#  Logging setup
# ============================================================

def setup_logging(config: dict) -> logging.Logger:
    level = getattr(logging, config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_file = config.get("LOG_FILE", "/var/log/cortex.log")

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file))
    except PermissionError:
        print(f"WARNING: Cannot write to log file {log_file}, logging to stdout only.")

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("cortex")


# ============================================================
#  State management
# ============================================================

def load_state(path: str) -> dict:
    """Load persistent state from JSON file."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerts": {}, "last_run": None, "run_count": 0}


def save_state(path: str, state: dict) -> None:
    """Save state to JSON file, creating parent directories if needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def is_in_cooldown(state: dict, issue_key: str, cooldown_minutes: int) -> bool:
    """Return True if this issue was already alerted within the cooldown window."""
    last_alert = state["alerts"].get(issue_key)
    if not last_alert:
        return False
    try:
        last_dt = datetime.fromisoformat(last_alert)
        return datetime.now() - last_dt < timedelta(minutes=cooldown_minutes)
    except (ValueError, TypeError):
        return False


def record_alert(state: dict, issue_key: str) -> None:
    """Record that an alert was sent for this issue."""
    state["alerts"][issue_key] = datetime.now().isoformat()


# ============================================================
#  Docker — container status and log collection
# ============================================================

def get_container_status(name: str) -> dict:
    """
    Return a dict with container status info via `docker inspect`.
    Returns {"running": False, "error": "..."} if container not found.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Status}}|{{.State.ExitCode}}|{{.RestartCount}}",
             name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"name": name, "running": False, "error": "container not found"}

        parts = result.stdout.strip().split("|")
        status     = parts[0] if len(parts) > 0 else "unknown"
        exit_code  = int(parts[1]) if len(parts) > 1 else -1
        restarts   = int(parts[2]) if len(parts) > 2 else 0

        return {
            "name":        name,
            "status":      status,
            "running":     status == "running",
            "exit_code":   exit_code,
            "restart_count": restarts,
        }
    except subprocess.TimeoutExpired:
        return {"name": name, "running": False, "error": "docker inspect timed out"}
    except FileNotFoundError:
        return {"name": name, "running": False, "error": "docker binary not found"}


def get_container_logs(name: str, lines: int) -> str:
    """Fetch the last N lines of logs from a running container."""
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), "--timestamps", name],
            capture_output=True, text=True, timeout=15
        )
        # Docker writes logs to stderr by default
        output = result.stderr if result.stderr else result.stdout
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"[ERROR] Log fetch timed out for container: {name}"
    except Exception as e:
        return f"[ERROR] Could not fetch logs for {name}: {e}"


def filter_noise(log_text: str, patterns: list[str], debug: bool = False) -> tuple[str, int]:
    """
    Remove lines matching any noise pattern.
    Returns (filtered_log, suppressed_count).
    """
    if not patterns or not log_text:
        return log_text, 0

    lines = log_text.splitlines()
    filtered = []
    suppressed = 0

    for line in lines:
        if any(p.lower() in line.lower() for p in patterns if p.strip()):
            suppressed += 1
        else:
            filtered.append(line)

    return "\n".join(filtered), suppressed


def collect_container_data(config: dict, logger: logging.Logger) -> tuple[str, list]:
    """
    Gather status and filtered logs for all monitored containers.
    Returns (prompt_section, critical_issues_from_docker).
    """
    container_names = config.get("MONITORED_CONTAINERS", "").split()
    log_lines       = int(config.get("LOG_LINES", 100))
    noise_patterns  = [p.strip() for p in config.get("NOISE_PATTERNS", "").split(",")]
    noise_debug     = config.get("NOISE_DEBUG", "false").lower() == "true"

    sections = []
    docker_criticals = []
    total_suppressed = 0

    for name in container_names:
        if not name:
            continue

        logger.debug(f"Inspecting container: {name}")
        status = get_container_status(name)

        # Flag containers that are not running
        if not status.get("running"):
            issue = f"Container '{name}' is {status.get('status', 'unknown')}"
            if status.get("exit_code", 0) != 0:
                issue += f" (exit code {status['exit_code']})"
            if status.get("restart_count", 0) > 0:
                issue += f" — restarted {status['restart_count']} times"
            docker_criticals.append(issue)
            logger.warning(issue)

        # Collect logs
        raw_logs = get_container_logs(name, log_lines)
        filtered_logs, suppressed = filter_noise(raw_logs, noise_patterns, noise_debug)
        total_suppressed += suppressed

        section = (
            f"=== CONTAINER: {name} | STATUS: {status.get('status', 'unknown').upper()} ===\n"
            f"{filtered_logs if filtered_logs else '[no log output after filtering]'}"
        )
        sections.append(section)

    if noise_debug:
        logger.info(f"Noise filter: suppressed {total_suppressed} lines across all containers")

    prompt_data = "\n\n".join(sections)
    return prompt_data, docker_criticals, total_suppressed


# ============================================================
#  LLM analysis via Ollama
# ============================================================

def query_ollama(config: dict, prompt: str, logger: logging.Logger) -> str | None:
    """
    Send log data to Ollama and return the model's analysis.
    Returns None on failure.
    """
    host    = config.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model   = config.get("OLLAMA_MODEL", "cortex:latest")
    timeout = int(config.get("OLLAMA_TIMEOUT", 60))
    url     = f"{host}/api/generate"

    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 8192,
        }
    }

    try:
        logger.debug(f"Querying Ollama at {url} with model {model}")
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to Ollama at {host}. Is it running?")
    except requests.exceptions.Timeout:
        logger.error(f"Ollama request timed out after {timeout}s. Consider increasing OLLAMA_TIMEOUT.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"Ollama HTTP error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error querying Ollama: {e}")

    return None


def build_prompt(log_data: str, docker_criticals: list, timestamp: str) -> str:
    """Compose the full prompt sent to the LLM."""
    critical_note = ""
    if docker_criticals:
        critical_note = (
            "\n## PRE-DETECTED DOCKER ISSUES (include these as CRITICAL)\n"
            + "\n".join(f"- {c}" for c in docker_criticals)
            + "\n"
        )

    return f"""Analyse the following homelab container logs and produce a Cortex monitoring report.
Current time: {timestamp}
{critical_note}
## CONTAINER LOGS

{log_data}

---
Produce the CORTEX REPORT in the exact format specified in your instructions. Do not add any text outside the report format.
"""


# ============================================================
#  Response parsing
# ============================================================

def parse_report(report_text: str) -> dict:
    """
    Extract counts and sections from a Cortex report.
    Returns {"critical": int, "warnings": int, "info": int, "raw": str}
    """
    result = {"critical": 0, "warnings": 0, "info": 0, "raw": report_text}

    for line in report_text.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("CRITICAL:"):
            try:
                result["critical"] = int(line_stripped.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line_stripped.startswith("WARNINGS:"):
            try:
                result["warnings"] = int(line_stripped.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line_stripped.startswith("INFO:"):
            try:
                result["info"] = int(line_stripped.split(":")[1].strip())
            except (ValueError, IndexError):
                pass

    return result


# ============================================================
#  ntfy notifications
# ============================================================

def send_ntfy(config: dict, title: str, message: str, priority: str = "default",
              logger: logging.Logger = None) -> bool:
    """
    Send a push notification via ntfy.
    priority: min | low | default | high | urgent
    """
    base_url = config.get("NTFY_URL", "").rstrip("/")
    if not base_url:
        if logger:
            logger.warning("NTFY_URL not configured — skipping notification")
        return False

    topic = config.get("NTFY_TOPIC_CRITICAL" if priority == "urgent" else "NTFY_TOPIC",
                       config.get("NTFY_TOPIC", "homelab-system"))
    url   = f"{base_url}/{topic}"

    headers = {
        "Title":    title,
        "Priority": priority,
        "Tags":     "computer,cortex",
    }
    token = config.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        if logger:
            logger.info(f"ntfy notification sent: [{priority}] {title}")
        return True
    except requests.exceptions.ConnectionError:
        if logger:
            logger.error(f"Cannot connect to ntfy at {base_url}")
    except requests.exceptions.HTTPError as e:
        if logger:
            logger.error(f"ntfy HTTP error: {e}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to send ntfy notification: {e}")

    return False


def route_alerts(parsed: dict, config: dict, state: dict,
                 dry_run: bool, logger: logging.Logger) -> None:
    """
    Decide whether to send notifications based on report content and cooldown rules.
    """
    cooldown = int(config.get("ALERT_COOLDOWN_MINUTES", 120))
    raw      = parsed["raw"]

    if parsed["critical"] > 0:
        issue_key = f"critical_{parsed['critical']}"
        if dry_run:
            logger.info(f"[DRY RUN] Would send CRITICAL alert ({parsed['critical']} issues)")
        elif not is_in_cooldown(state, issue_key, cooldown):
            # Extract just the critical section for the notification body
            lines = raw.splitlines()
            in_critical = False
            critical_lines = []
            for line in lines:
                if line.startswith("CRITICAL:"):
                    in_critical = True
                elif line.startswith("WARNINGS:") or line.startswith("INFO:"):
                    in_critical = False
                if in_critical and line.strip() and not line.startswith("CRITICAL:"):
                    critical_lines.append(line.strip())

            body = "\n".join(critical_lines) if critical_lines else raw[:500]
            sent = send_ntfy(
                config,
                title=f"🔴 Cortex — {parsed['critical']} critical issue(s)",
                message=body,
                priority="urgent",
                logger=logger,
            )
            if sent:
                record_alert(state, issue_key)
        else:
            logger.info(f"Critical alert suppressed (cooldown active for {cooldown} min)")

    elif parsed["warnings"] > 0:
        issue_key = f"warnings_{parsed['warnings']}"
        if dry_run:
            logger.info(f"[DRY RUN] Would send WARNING alert ({parsed['warnings']} warnings)")
        elif not is_in_cooldown(state, issue_key, cooldown):
            sent = send_ntfy(
                config,
                title=f"🟡 Cortex — {parsed['warnings']} warning(s)",
                message=raw[:800],
                priority="high",
                logger=logger,
            )
            if sent:
                record_alert(state, issue_key)
        else:
            logger.info(f"Warning alert suppressed (cooldown active for {cooldown} min)")

    else:
        logger.info("No actionable issues found — no notification sent")


# ============================================================
#  Prometheus metrics state
# ============================================================

def update_metrics(parsed: dict, suppressed: int, container_count: int) -> None:
    """
    Write current run metrics to a JSON file for cortex-exporter.py to serve.
    """
    Path(METRICS_FILE).parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(METRICS_FILE) as f:
            metrics = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        metrics = {
            "cortex_alerts_total":        0,
            "cortex_noise_filtered_total": 0,
        }

    metrics["cortex_last_run_timestamp"]   = int(time.time())
    metrics["cortex_containers_monitored"] = container_count
    metrics["cortex_noise_filtered_total"] = metrics.get("cortex_noise_filtered_total", 0) + suppressed

    if parsed["critical"] > 0 or parsed["warnings"] > 0:
        metrics["cortex_alerts_total"] = metrics.get("cortex_alerts_total", 0) + 1

    with open(METRICS_FILE, "w") as f:
        json.dump(metrics, f, indent=2)


# ============================================================
#  Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Cortex homelab monitor")
    parser.add_argument("--config",  default=DEFAULT_CONFIG, help="Path to cortex.conf")
    parser.add_argument("--dry-run", action="store_true",    help="Analyse but do not send alerts")
    parser.add_argument("--version", action="store_true",    help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"Cortex monitor v{VERSION}")
        sys.exit(0)

    # Bootstrap
    config = load_config(args.config)
    logger = setup_logging(config)
    logger.info(f"Cortex monitor v{VERSION} starting (dry_run={args.dry_run})")

    # State
    state_path = config.get("STATE_FILE", DEFAULT_STATE)
    state      = load_state(state_path)
    state["run_count"] = state.get("run_count", 0) + 1
    state["last_run"]  = datetime.now().isoformat()

    # Collect data
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info("Collecting container logs...")
    log_data, docker_criticals, suppressed = collect_container_data(config, logger)

    container_count = len(config.get("MONITORED_CONTAINERS", "").split())
    logger.info(f"Collected logs from {container_count} containers, {suppressed} lines filtered")

    # Build prompt and query LLM
    prompt = build_prompt(log_data, docker_criticals, timestamp)
    logger.info("Querying Ollama for analysis...")
    response = query_ollama(config, prompt, logger)

    if response is None:
        logger.error("LLM analysis failed — sending fallback alert if criticals detected")
        if docker_criticals:
            body = "\n".join(f"- {c}" for c in docker_criticals)
            if not args.dry_run:
                send_ntfy(
                    config,
                    title="🔴 Cortex — Docker issues detected (LLM offline)",
                    message=body,
                    priority="urgent",
                    logger=logger,
                )
        save_state(state_path, state)
        sys.exit(1)

    logger.debug(f"LLM response:\n{response}")

    # Parse and route
    parsed = parse_report(response)
    logger.info(
        f"Report: {parsed['critical']} critical, "
        f"{parsed['warnings']} warnings, "
        f"{parsed['info']} info"
    )

    route_alerts(parsed, config, state, args.dry_run, logger)

    # Update metrics and persist state
    update_metrics(parsed, suppressed, container_count)
    save_state(state_path, state)

    logger.info("Cortex monitor run complete")


if __name__ == "__main__":
    main()
