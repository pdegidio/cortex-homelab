# Changelog

All notable changes to Cortex will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2024-04-17

### Added

**Core monitoring (`cortex-monitor.py`)**
- Docker container status inspection via `docker inspect` — detects crashes, unexpected restarts, and non-zero exit codes
- Log collection from all configured containers via `docker logs --tail`
- Configurable noise filter: suppress known non-actionable log patterns before LLM analysis
- LLM-powered log analysis via Ollama API (`/api/generate`) — semantic analysis, not just threshold alerting
- Structured report parsing: extracts CRITICAL / WARNING / INFO counts from LLM output
- ntfy push notifications with priority routing — urgent for critical issues, high for warnings
- Per-issue cooldown system: prevents alert storms when the same problem persists across cycles
- Fallback alert path: if Ollama is offline, Docker-detected crashes still trigger ntfy
- `--dry-run` flag: full analysis cycle without sending any notifications
- Prometheus metrics state file (`/var/lib/cortex/metrics.json`) written after every run
- State persistence (`/var/lib/cortex/state.json`): tracks alert history and cooldown timestamps across reboots

**Daily digest (`cortex-digest.py`)**
- Collects logs from the past 24 hours (configurable via `--since`) for all monitored containers
- Applies same noise filter as monitor for consistent analysis
- LLM-generated narrative digest: trends, completed operations, single actionable recommendation
- Fallback raw-status digest when Ollama is unavailable
- Digest history log (`/var/lib/cortex/digest_history.json`): retains last 30 daily digests
- ntfy message size capping with graceful truncation (4096-byte limit)
- System health inference from digest text: HEALTHY / DEGRADED / CRITICAL
- `--dry-run` flag: prints digest to stdout without sending

**Prometheus exporter (`cortex-exporter.py`)**
- HTTP server exposing Prometheus text format on port 9192 (configurable)
- Metrics exposed:
  - `cortex_exporter_up` — exporter liveness
  - `cortex_last_run_timestamp` — Unix timestamp of last monitor run
  - `cortex_last_run_age_seconds` — seconds since last monitor run (alert on stale cycles)
  - `cortex_containers_monitored` — number of containers under watch
  - `cortex_alerts_total` — cumulative alert count (counter)
  - `cortex_noise_filtered_total` — cumulative noise-suppressed lines (counter)
  - `cortex_digest_last_sent` — Unix timestamp of last digest
  - `cortex_digest_age_seconds` — seconds since last digest
  - `cortex_system_health` — numeric health (0=HEALTHY, 1=DEGRADED, 2=CRITICAL)
  - `cortex_build_info` — version label gauge
- `/health` and `/healthz` endpoints for Docker/uptime monitoring
- `--once` flag: prints metrics to stdout and exits (for testing and scripting)
- Zero external dependencies — stdlib only

**AI Gateway (`ai-gateway/`)**
- nginx reverse proxy in front of Ollama
- Rate limiting on `/api/generate` (10 req/min, burst 5) — prevents runaway inference
- Stable internal hostname (`ai-gateway`) for Docker Compose environments
- Request timing log format for debugging slow inference
- Docker Compose with health check and dedicated `cortex-net` network

**Grafana dashboard (`grafana/cortex-monitor.json`)**
- 13-panel dashboard, ready to import (Grafana 10+)
- Panels: System Health, Last Monitor Run, Containers Monitored, Total Alerts, Noise Filtered, Last Digest, Monitor Freshness gauge, Alert Rate timeseries, Noise Filtered timeseries, System Health Over Time, Monitor Run Age, Exporter Status, Build Info
- Colour-coded thresholds throughout: green / yellow / red
- 1-minute auto-refresh, 24h default time range

**Configuration (`cortex.conf.example`)**
- Fully documented key=value configuration file
- Sections: Ollama, ntfy, Docker, \*arr services, Noise filtering, Scheduling, Prometheus, Logging
- Optional services (Readarr, Lidarr) present but blank — visible but non-intrusive

**Ollama Modelfile**
- Infrastructure-aware system prompt with deep context on the homelab \*arr stack
- Explicit NOISE list: patterns to ignore before alerting
- Explicit SIGNAL list: what always warrants a notification
- Dual output format: real-time report (every 30 min) and daily digest (narrative)
- Temperature 0.2 — deterministic, consistent output for monitoring use

**Installer (`install.sh`)**
- Interactive installation with coloured output and step-by-step prompts
- Checks: Python 3.10+, Docker, requests library
- Creates dedicated `cortex` system user with minimal privileges
- Installs scripts to `/opt/cortex/`, state to `/var/lib/cortex/`
- Interactive configuration of Ollama host, ntfy, and container list
- Optional Ollama model pull and build (`ollama create cortex`)
- Cron job setup for monitor (every 30 min) and digest (09:00 daily)
- Systemd service installation for exporter (with fallback to @reboot cron)
- Dry-run test cycle at end of installation

**systemd service (`docs/systemd-exporter.service`)**
- Automatic restart on failure (max 5 attempts per 60 seconds)
- Security hardening: `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`
- Resource limits: 64MB RAM, 10% CPU
- Annotated with `cortex` user setup instructions

**Documentation**
- `README.md` (English) and `README.it.md` (Italian)
- Architecture diagram, component table, Quick Start (8 steps + TL;DR)
- Noise filtering section with default pattern list
- Prometheus metrics reference table
- Upgrade path to Cortex Core / Pro

---

## [Unreleased]

### Planned
- `cortex.conf` validation script (`cortex-check.py`) — verify all settings before first run
- Multi-host monitoring support — monitor containers on remote Docker hosts via SSH
- Webhook support as alternative to ntfy
- Cortex Core: full \*arr + VPN media stack template
- Cortex Pro: complete integrated bundle with Grafana dashboard suite

---

[1.0.0]: https://github.com/pdegidio/cortex-homelab/releases/tag/v1.0.0
[Unreleased]: https://github.com/pdegidio/cortex-homelab/compare/v1.0.0...HEAD
