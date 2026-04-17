# 🧠 Cortex — Monitoraggio Homelab con AI

> Monitoring intelligente per infrastrutture self-hosted. Cortex osserva il tuo stack Docker, analizza i log dei servizi *arr e genera digest giornalieri — tramite un LLM locale che gira sul tuo hardware.

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![Ollama](https://img.shields.io/badge/LLM-Ollama-orange)
![Prometheus](https://img.shields.io/badge/Metriche-Prometheus-red)

---

## Perché Cortex?

La maggior parte degli strumenti di monitoring per homelab guarda i numeri — CPU%, RAM, disco. Cortex guarda il *significato*. Invece di scattare su soglie, legge i tuoi log come faresti tu: capisce il contesto, sopprime il rumore, e porta in superficie solo quello che merita attenzione.

- **Analisi semantica dei log** — un LLM locale legge i tuoi log *arr, non si limita a contarli
- **Nessuna dipendenza cloud** — Ollama gira sul tuo hardware, i tuoi dati restano nella tua rete
- **Costruito per stack media** — sa già cosa ti stanno dicendo Sonarr, Radarr, Prowlarr e qBittorrent

---

## Cos'è Cortex?

Cortex è uno strato di monitoring progettato specificamente per homelab che eseguono stack media basati su Docker. Collega un LLM locale (tramite Ollama) alla tua infrastruttura, abilitando:

- **Analisi dei trend** ogni 30 minuti su tutti i container attivi
- **Analisi dei log** per Sonarr, Radarr, Prowlarr e altri servizi *arr
- **Digest giornaliero** inviato via ntfy con riepiloghi e azioni suggerite
- **Export metriche Prometheus** per dashboard Grafana
- **Filtro del rumore** — solo gli alert che contano davvero

Cortex non è un servizio cloud. Tutto gira sul tuo hardware. Nessuna telemetria, nessun abbonamento, nessun dato che lascia la tua rete.

---

## Architettura

```
┌─────────────────────────────────────────────────────┐
│                   Il tuo Homelab                    │
│                                                     │
│  Stack Docker ──► Cortex Monitor ──► Ollama (LLM)  │
│       │                  │                          │
│  Log *arr          State File                       │
│       │                  │                          │
│       └──────────────────┼──► ntfy (notifiche)     │
│                          │                          │
│                    Prometheus ──► Grafana           │
└─────────────────────────────────────────────────────┘
```

**Componenti inclusi in questo repo (tier Free):**

| Componente | Descrizione |
|---|---|
| `cortex-monitor.py` | Script di monitoring principale, eseguito ogni 30 min via cron |
| `cortex-digest.py` | Generatore del riepilogo giornaliero, inviato via ntfy |
| `cortex-exporter.py` | Exporter metriche Prometheus (porta 9192) |
| `ai-gateway/` | Docker Compose — gateway HTTP verso Ollama |
| `grafana/` | Dashboard JSON — 13 pannelli, pronta all'importazione |
| `modelfile/` | Ollama Modelfile per un LLM consapevole dell'infrastruttura |

---

## Screenshot

![Cortex Grafana Dashboard](docs/screenshot-dashboard.png)

> *Dashboard Grafana con stato dei container, trend degli alert e storico dei digest giornalieri.*

---

## Requisiti

- Host Linux (Debian 12 / Ubuntu 22.04+ raccomandato)
- Docker + Docker Compose v2
- [Ollama](https://ollama.com/) in esecuzione localmente o su workstation GPU
- Prometheus + Grafana (opzionale, per le dashboard)
- Istanza ntfy (self-hosted o ntfy.sh)
- Python 3.10+

**Hardware raccomandato per l'inferenza LLM:**
- Solo CPU: minimo 16GB RAM — esegue `qwen2.5:7b` in modo accettabile
- GPU: 8GB VRAM — esegue `qwen2.5:14b` comodamente (raccomandato)

**Testato su:** Debian 12.5, Docker 26.1, Ollama 0.3.x, Python 3.11

---

## Avvio Rapido

**In breve:** clona il repo → scarica il modello → copia `cortex.conf.example` → imposta i nomi dei container e le API key → installa i cron job. Pronto in ~15 minuti.

---

### 1. Clona il repository

```bash
git clone https://github.com/pdegidio/cortex-homelab.git
cd cortex-homelab
```

### 2. Scarica il modello LLM

```bash
ollama pull qwen2.5:14b-instruct
```

### 3. Crea il modelfile consapevole dell'infrastruttura

```bash
cd modelfile/
ollama create cortex -f Modelfile
```

### 4. Configura il tuo ambiente

```bash
cp config/cortex.conf.example config/cortex.conf
nano config/cortex.conf
```

Impostazioni principali da aggiornare:

```ini
# Endpoint Ollama (locale o workstation GPU remota)
OLLAMA_HOST=http://192.168.1.x:11434
OLLAMA_MODEL=cortex:latest

# Configurazione ntfy
NTFY_URL=http://tua-istanza-ntfy:8090
NTFY_TOPIC=homelab-system

# Nomi container da monitorare (separati da spazio)
MONITORED_CONTAINERS="sonarr radarr prowlarr qbittorrent plex"

# Chiavi API *arr
SONARR_URL=http://localhost:8989
SONARR_API_KEY=la_tua_chiave
RADARR_URL=http://localhost:7878
RADARR_API_KEY=la_tua_chiave
```

### 5. Deploy dell'AI gateway

```bash
cd ai-gateway/
docker compose up -d
```

### 6. Installa gli script di monitoring

```bash
cp scripts/*.py /opt/scripts/
chmod +x /opt/scripts/cortex-*.py
```

### 7. Configura i cron job

```bash
crontab -e
```

Aggiungi le seguenti righe:

```cron
# Cortex — AI monitoring ogni 30 minuti
*/30 * * * * /usr/bin/python3 /opt/scripts/cortex-monitor.py >> /var/log/cortex.log 2>&1

# Cortex — Digest giornaliero alle 09:00
0 9 * * * /usr/bin/python3 /opt/scripts/cortex-digest.py >> /var/log/cortex-digest.log 2>&1

# Cortex — Prometheus exporter (mantieni attivo)
@reboot /usr/bin/python3 /opt/scripts/cortex-exporter.py &
```

> **Nota:** Il cron `@reboot` funziona per la maggior parte dei setup. Per ambienti sempre attivi o di produzione, un'unità systemd è più affidabile — vedi `docs/systemd-exporter.service` per un template già pronto.

### 8. Importa la dashboard Grafana

In Grafana → Dashboard → Importa → Carica file JSON → seleziona `grafana/cortex-monitor.json`

---

## Filtro del Rumore

Cortex include un filtro preconfigurato che sopprime le voci di log note e non utilizzabili. La lista di filtri predefinita copre:

- Operazioni di lettura metadati ffprobe
- Scansioni di routine di VideoFileInfoReader
- Rate limiting HTTP 429 dagli indexer
- Avvisi file torrent non validi
- Rumore degli health check Prowlarr su 9696/

La lista dei filtri è completamente personalizzabile in `config/cortex.conf`:

```ini
NOISE_PATTERNS="ffprobe,VideoFileInfoReader,429,invalid torrent,9696/"
```

---

## Metriche Prometheus

L'exporter espone le seguenti metriche sulla porta `9192`:

| Metrica | Descrizione |
|---|---|
| `cortex_alerts_total` | Totale alert generati |
| `cortex_last_run_timestamp` | Timestamp Unix dell'ultima esecuzione |
| `cortex_containers_monitored` | Numero di container monitorati |
| `cortex_digest_last_sent` | Timestamp Unix dell'ultimo digest inviato |
| `cortex_noise_filtered_total` | Voci di log soppresse dal filtro |

---

## Struttura delle Directory

```
cortex-homelab/
├── ai-gateway/
│   └── docker-compose.yml
├── config/
│   └── cortex.conf.example
├── docs/
│   ├── screenshot-dashboard.png
│   └── systemd-exporter.service
├── grafana/
│   └── cortex-monitor.json
├── modelfile/
│   └── Modelfile
├── scripts/
│   ├── cortex-monitor.py
│   ├── cortex-digest.py
│   └── cortex-exporter.py
└── README.it.md
```

---

## Passare a Cortex Core / Pro

Il tier gratuito copre il monitoring AI. Lo stack Cortex completo include:

**Cortex Core** — Stack media *arr + VPN completo
- Docker Compose preconfigurato per Sonarr, Radarr, Prowlarr, qBittorrent + VPN Gluetun
- Authelia 2FA + NGINX Proxy Manager con 22 host proxy preconfigurati
- Integrazione ntfy per tutte le app *arr, Tautulli, Uptime Kuma
- Oltre 30 gotcha documentati con soluzioni (esaurimento subnet Docker, remux HE-AAC, namespace di rete Gluetun, e molto altro)
- README completo in inglese e italiano

**Cortex Pro** — Core + monitoring AI, completamente integrato
- Tutto quello che c'è in Core
- Tutto quello che c'è in questo repo, già collegato allo stack completo
- Bundle dashboard Grafana (media, sistema, monitoring AI)
- Ollama Modelfile custom con conoscenza dell'infrastruttura integrata
- Guida setup passo-passo da zero a completamente operativo

Disponibile su: **[link gumroad]**

---

## Contribuire

Issue e pull request sono benvenuti. Se Cortex ti è utile, considera di mettere una stella al repo — aiuta altri a trovarlo.

---

## Licenza

MIT — usalo, modificalo, distribuiscilo. L'attribuzione è apprezzata ma non obbligatoria.
