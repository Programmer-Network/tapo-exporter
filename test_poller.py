#!/usr/bin/env python3
"""Regression test for the ~50% poll-success bug.

In the field, every Tapo session survived exactly one poll and then answered
403 Forbidden / Tapo(SessionTimeout). The old poll() reacted by clearing the
device and giving up until the NEXT cycle, so the loop alternated success,
expiry, re-handshake, expiry — pinning `avg_over_time(tapo_plug_up[3h])` at
0.503 on all three plugs while power gauges kept updating from the good half.

This test models exactly that plug: a session that dies after one full read.
The poller must absorb it and report every poll as successful.

Run: python3 test_poller.py   (needs prometheus-client; no network, no plugs)
"""
import asyncio
import importlib.util
import os
import sys
import types


# ── a plug whose session dies after exactly one complete read ────────────────
class FakeInfo:
    device_on, rssi, on_time = True, -55, 1234


class FakeEnergy:
    today_energy, month_energy = 612.0, 18000.0


class FakePower:
    current_power = 50.0


class FakeDevice:
    def __init__(self):
        self.spent = False

    async def _guard(self):
        if self.spent:
            raise RuntimeError("Tapo(SessionTimeout): 403 Forbidden")

    async def get_device_info(self):
        await self._guard()
        return FakeInfo()

    async def get_energy_usage(self):
        await self._guard()
        return FakeEnergy()

    async def get_current_power(self):
        await self._guard()
        self.spent = True          # the read that consumes the session
        return FakePower()


class FakeApiClient:
    def __init__(self, user, password):
        self.handshakes = 0

    async def p110(self, host):
        self.handshakes += 1
        return FakeDevice()


# The exporter is a single script, not a package — stub `tapo` before loading it.
fake_tapo = types.ModuleType("tapo")
fake_tapo.ApiClient = FakeApiClient
sys.modules["tapo"] = fake_tapo

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "tapo_exporter", os.path.join(_here, "tapo-exporter.py"))
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)

PLUG = "DGX Spark"


def gauge(metric):
    return metric.labels(plug=PLUG)._value.get()


async def test_expiring_session_still_polls_cleanly():
    poller = exporter.PlugPoller("user", "password", PLUG, "192.168.10.217")

    ups = []
    for _ in range(10):
        await poller.poll()
        ups.append(gauge(exporter.metric_up))

    rate = sum(ups) / len(ups)
    print(f"up per poll  : {[int(u) for u in ups]}")
    print(f"success rate : {rate:.0%}")
    print(f"reauths      : {int(gauge(exporter.metric_reauths))}")
    print(f"hard failures: {int(gauge(exporter.metric_fails))}")

    assert rate == 1.0, f"expected every poll to succeed, got {rate:.0%}"
    assert gauge(exporter.metric_fails) == 0, "a recoverable expiry was reported as a failure"
    assert gauge(exporter.metric_power) == 50.0, "power gauge not populated"
    assert gauge(exporter.metric_last_ok) > 0, "last-success timestamp not set"


async def test_unreachable_plug_is_reported_down():
    """The retry must not paper over a plug that is genuinely gone."""
    class DeadClient(FakeApiClient):
        async def p110(self, host):
            raise RuntimeError("connection refused")

    poller = exporter.PlugPoller("user", "password", PLUG, "192.168.10.217")
    poller.client = DeadClient("user", "password")
    before = gauge(exporter.metric_fails)
    await poller.poll()

    assert gauge(exporter.metric_up) == 0, "unreachable plug should report up=0"
    assert gauge(exporter.metric_fails) == before + 1, "failure counter not incremented"
    print("unreachable plug correctly reported down")


async def main():
    await test_expiring_session_still_polls_cleanly()
    await test_unreachable_plug_is_reported_down()
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
