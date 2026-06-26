#!/usr/bin/env python3
"""Tapo P110 Prometheus exporter — polls smart plugs and exposes metrics via HTTP.
   Usage: tapo-exporter.py [--port PORT] [--interval SECONDS]
"""
import asyncio
import os
import sys
import time
import argparse

from tapo import ApiClient
from prometheus_client import Gauge, CollectorRegistry, start_http_server

# ── plug definitions ──────────────────────────────────────────
PLUGS = {
    "head":   "192.168.10.216",
    "worker": "192.168.10.217",
    "pis":    "192.168.10.215",
}

# ── Prometheus metrics ─────────────────────────────────────────
registry = CollectorRegistry()

metric_up       = Gauge("tapo_plug_up",         "1 if plug is reachable",         ["plug"], registry=registry)
metric_state    = Gauge("tapo_plug_state",      "1=on, 0=off",                    ["plug"], registry=registry)
metric_power    = Gauge("tapo_plug_power_watts", "Current power draw in watts",    ["plug"], registry=registry)
metric_rssi     = Gauge("tapo_plug_rssi_dbm",   "Wi-Fi signal strength in dBm",   ["plug"], registry=registry)
metric_on_since = Gauge("tapo_plug_on_since_seconds", "Seconds since powered on",  ["plug"], registry=registry)
metric_today    = Gauge("tapo_plug_energy_today_kwh", "Energy today in kWh",       ["plug"], registry=registry)
metric_month    = Gauge("tapo_plug_energy_month_kwh", "Energy this month in kWh",  ["plug"], registry=registry)

# ── polling ────────────────────────────────────────────────────
async def poll_plug(name: str, host: str):
    """Poll a single plug and update Prometheus gauges."""
    labels = {"plug": name}
    try:
        client = ApiClient(os.environ["KASA_USERNAME"], os.environ["KASA_PASSWORD"])
        device = await client.p110(host)
        info = await device.get_device_info()
        energy = await device.get_energy_usage()

        metric_up.labels(**labels).set(1)
        metric_state.labels(**labels).set(1 if info.device_on else 0)
        metric_power.labels(**labels).set(energy.current_power / 1000.0)
        metric_rssi.labels(**labels).set(info.rssi)
        metric_on_since.labels(**labels).set(info.on_time)
        metric_today.labels(**labels).set(energy.today_energy / 1000.0)
        metric_month.labels(**labels).set(energy.month_energy / 1000.0)
    except Exception:
        metric_up.labels(**labels).set(0)


async def poll_all():
    """Poll all plugs concurrently."""
    tasks = [poll_plug(name, host) for name, host in PLUGS.items()]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── main ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Tapo P110 Prometheus exporter")
    parser.add_argument("--port", type=int, default=9801, help="HTTP listen port (default: 9801)")
    parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds (default: 15)")
    args = parser.parse_args()

    if "KASA_USERNAME" not in os.environ or "KASA_PASSWORD" not in os.environ:
        print("ERROR: KASA_USERNAME and KASA_PASSWORD must be set in environment", file=sys.stderr)
        print("       source ~/.config/tapo/creds.env before running", file=sys.stderr)
        sys.exit(1)

    # initial poll
    asyncio.run(poll_all())

    # prometheus_client's built-in HTTP server (runs in daemon thread)
    start_http_server(args.port, registry=registry)
    print(f"tapo-exporter listening on :{args.port}/metrics, polling every {args.interval}s", file=sys.stderr, flush=True)

    # polling loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        time.sleep(args.interval)
        loop.run_until_complete(poll_all())


if __name__ == "__main__":
    main()
