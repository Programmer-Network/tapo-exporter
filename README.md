# Tapo Exporter

Prometheus exporter for TP-Link Tapo P110 smart plugs. Polls plug state, power draw, energy usage, and Wi-Fi signal strength, exposing them as Prometheus metrics.

## Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `tapo_plug_up` | `plug=head\|worker\|pis` | 1 if reachable |
| `tapo_plug_state` | `plug=head\|worker\|pis` | 1=on, 0=off |
| `tapo_plug_power_watts` | `plug=head\|worker\|pis` | Current power draw |
| `tapo_plug_rssi_dbm` | `plug=head\|worker\|pis` | Wi-Fi signal strength |
| `tapo_plug_on_since_seconds` | `plug=head\|worker\|pis` | Seconds since powered on |
| `tapo_plug_energy_today_kwh` | `plug=head\|worker\|pis` | Energy consumption today |
| `tapo_plug_energy_month_kwh` | `plug=head\|worker\|pis` | Energy consumption this month |

## Deployment

Designed for the Programmer Network homelab k3s cluster, deployed via ArgoCD app-of-apps.

### Prerequisites

- Vault secret at `kv-v2/tapo` with keys `username` and `password`
- Vault auth + connection configured in the monitoring namespace

### Vault Secret

```bash
vault kv put secret/monitoring/tapo username="<tapo-account-email>" password="<tapo-password>"
```

### ArgoCD

1. Add `argocd-application.yaml` to the [argocd-app-of-apps](https://github.com/Programmer-Network/argocd-app-of-apps) repo under `apps/observability/`
2. Register it in `root-projects-app.yaml`
3. Push — ArgoCD syncs automatically

### Local Development

```bash
# Install deps
pip install tapo prometheus-client

# Set credentials
export KASA_USERNAME="<tapo-account-email>"
export KASA_PASSWORD="..."

# Run
python3 tapo-exporter.py --port 9801 --interval 15

# Test
curl http://localhost:9801/metrics
```
