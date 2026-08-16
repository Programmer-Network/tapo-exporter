#!/usr/bin/env python3
"""Tapo P110 Prometheus exporter — polls smart plugs and exposes metrics via HTTP.

Config (env or flags):
  KASA_USERNAME / KASA_PASSWORD   Tapo account credentials (required)
  TAPO_PLUGS                      "name=ip,name=ip,..." (default: built-in list)
  TAPO_PORT / --port              HTTP listen port (default 9801)
  TAPO_INTERVAL / --interval      poll interval seconds (default 15)
  TAPO_OP_TIMEOUT                 per-call timeout seconds (default 10)
"""
import argparse
import asyncio
import logging
import os
import sys
import time

from tapo import ApiClient
from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("tapo-exporter")

DEFAULT_PLUGS = {
    "head":   "192.168.10.216",
    "worker": "192.168.10.217",
    "pis":    "192.168.10.215",
}

OP_TIMEOUT = float(os.environ.get("TAPO_OP_TIMEOUT", "10"))

# ── Prometheus metrics ─────────────────────────────────────────
registry = CollectorRegistry()
metric_up       = Gauge("tapo_plug_up",               "1 if plug is reachable",       ["plug"], registry=registry)
metric_state    = Gauge("tapo_plug_state",            "1=on, 0=off",                  ["plug"], registry=registry)
metric_power    = Gauge("tapo_plug_power_watts",      "Current power draw in watts",  ["plug"], registry=registry)
metric_rssi     = Gauge("tapo_plug_rssi_dbm",         "Wi-Fi signal strength in dBm", ["plug"], registry=registry)
metric_on_since = Gauge("tapo_plug_on_since_seconds", "Seconds since powered on",     ["plug"], registry=registry)
metric_today    = Gauge("tapo_plug_energy_today_kwh", "Energy today in kWh",          ["plug"], registry=registry)
metric_month    = Gauge("tapo_plug_energy_month_kwh", "Energy this month in kWh",     ["plug"], registry=registry)
# On failure the gauges above keep their last good values rather than going
# stale-but-plausible in silence. This is how a consumer tells the difference:
# time() - tapo_plug_last_success_seconds is the true age of every reading.
metric_last_ok  = Gauge("tapo_plug_last_success_seconds", "Unix time of the last successful poll", ["plug"], registry=registry)
metric_reauths  = Counter("tapo_plug_reauth_total",   "Mid-poll re-handshakes after a dropped session", ["plug"], registry=registry)
metric_fails    = Counter("tapo_plug_poll_failures_total", "Polls that failed even after a re-handshake", ["plug"], registry=registry)


def load_plugs() -> dict:
    raw = os.environ.get("TAPO_PLUGS", "").strip()
    if not raw:
        return dict(DEFAULT_PLUGS)
    plugs = {}
    for pair in raw.split(","):
        if "=" in pair:
            name, host = pair.split("=", 1)
            plugs[name.strip()] = host.strip()
    return plugs or dict(DEFAULT_PLUGS)


class PlugPoller:
    """Holds a reused device session per plug, re-handshaking in place on error.

    Each poller owns its own ApiClient. Sharing one across plugs makes their
    handshakes race — the plugs answer 403 Forbidden for every request carrying
    a session another plug's handshake has since superseded.
    """

    def __init__(self, user: str, password: str, name: str, host: str):
        self.client = ApiClient(user, password)
        self.name = name
        self.host = host
        self.device = None

    async def _read(self):
        """One full read against the current session, handshaking if needed."""
        if self.device is None:
            self.device = await asyncio.wait_for(self.client.p110(self.host), OP_TIMEOUT)
        info = await asyncio.wait_for(self.device.get_device_info(), OP_TIMEOUT)
        energy = await asyncio.wait_for(self.device.get_energy_usage(), OP_TIMEOUT)
        power = await asyncio.wait_for(self.device.get_current_power(), OP_TIMEOUT)
        return info, energy, power

    async def poll(self):
        labels = {"plug": self.name}
        try:
            try:
                info, energy, power = await self._read()
            except Exception as first:
                # A dropped session is the overwhelmingly common failure here and
                # it is recoverable immediately — the plug is fine, our token is
                # not. Retrying on the NEXT cycle instead of this one is what
                # pinned poll success at ~50%: every session survived exactly one
                # poll, so the loop alternated success, expiry, re-handshake,
                # expiry, forever. Reconnect and retry once, in place.
                log.info("session lost for plug %s (%s): %s — re-handshaking",
                         self.name, self.host, first)
                metric_reauths.labels(**labels).inc()
                self.device = None
                info, energy, power = await self._read()

            metric_up.labels(**labels).set(1)
            metric_state.labels(**labels).set(1 if info.device_on else 0)
            metric_power.labels(**labels).set(power.current_power)             # already in watts
            metric_rssi.labels(**labels).set(info.rssi)
            metric_on_since.labels(**labels).set(info.on_time)
            metric_today.labels(**labels).set(energy.today_energy / 1000.0)    # Wh -> kWh
            metric_month.labels(**labels).set(energy.month_energy / 1000.0)    # Wh -> kWh
            metric_last_ok.labels(**labels).set(time.time())
        except Exception as e:
            # Both the original attempt and the retry failed, so this is not a
            # stale session — the plug is genuinely unreachable, off the Wi-Fi,
            # or the credentials are wrong.
            metric_up.labels(**labels).set(0)
            metric_fails.labels(**labels).inc()
            self.device = None  # force a fresh handshake next cycle
            log.warning("poll failed for plug %s (%s) after re-handshake: %s",
                        self.name, self.host, e)


async def poll_loop(pollers, interval: int):
    while True:
        await asyncio.gather(*(p.poll() for p in pollers), return_exceptions=True)
        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Tapo P110 Prometheus exporter")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TAPO_PORT", "9801")))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("TAPO_INTERVAL", "15")))
    args = parser.parse_args()

    user = os.environ.get("KASA_USERNAME")
    password = os.environ.get("KASA_PASSWORD")
    if not user or not password:
        log.error("KASA_USERNAME and KASA_PASSWORD must be set in the environment")
        sys.exit(1)

    plugs = load_plugs()
    pollers = [PlugPoller(user, password, name, host) for name, host in plugs.items()]

    # Start the HTTP server FIRST (daemon thread) so /metrics — and thus the k8s
    # readiness probe — is serving immediately, even if the plugs are unreachable.
    start_http_server(args.port, registry=registry)
    log.info("tapo-exporter listening on :%d/metrics, polling %d plug(s) every %ds",
             args.port, len(pollers), args.interval)

    asyncio.run(poll_loop(pollers, args.interval))


if __name__ == "__main__":
    main()
