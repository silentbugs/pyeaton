"""
Shutdown policy, distilled from scripts/managers/shutdown_manager.js and the
recovered config.js `shutdownSettings`:

  runTimeToEmptyLimit : begin OS shutdown when runtime left <= this (seconds)
  shutdownDuration    : grace period handed to the UPS (DelayBeforeShutdown)
  shutdownType        : what to do to the host ("none" | "hibernate" | "shutdown" | "script")
  shutdownScript      : script to run when shutdownType == "script"

For safety this reimplementation NEVER powers off the host by default
(`shutdownType="none"`): it logs the decision and records it in status so you
can see the policy fire in the dashboard. Set execute=True + a real type to
actually act.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass

log = logging.getLogger("shutdown")


@dataclass
class ShutdownConfig:
    run_time_to_empty_limit: int = 300  # seconds (matches recovered default)
    shutdown_duration: int = 120        # seconds grace for the UPS
    shutdown_type: str = "none"         # none|hibernate|shutdown|script
    shutdown_script: str = ""
    execute: bool = False               # master safety switch


class ShutdownManager:
    def __init__(self, cfg: ShutdownConfig, driver) -> None:
        self.cfg = cfg
        self.driver = driver
        self.triggered = False

    def evaluate(self, status) -> dict:
        """Decide whether to start a shutdown. Returns a status fragment."""
        ac = status.get("UPS.PowerSummary.PresentStatus.ACPresent", 1)
        rtte = status.get("UPS.PowerSummary.RunTimeToEmpty", None)
        imminent = status.get("UPS.PowerSummary.PresentStatus.ShutdownImminent", 0)
        below = status.get("UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit", 0)

        reached = False
        reason = ""
        if ac == 0 and rtte is not None:
            if rtte <= self.cfg.run_time_to_empty_limit:
                reached, reason = True, f"runtime {rtte}s <= limit {self.cfg.run_time_to_empty_limit}s"
            elif imminent:
                reached, reason = True, "UPS signalled ShutdownImminent"
            elif below:
                reached, reason = True, "below remaining-capacity limit"

        frag = {
            "Local.ShutdownReached": 1 if reached else 0,
            "Local.RunTimeToShutdown": (rtte - self.cfg.run_time_to_empty_limit)
            if (rtte is not None) else -1,
        }

        if reached and not self.triggered:
            self.triggered = True
            self._fire(reason)
        if ac == 1:
            self.triggered = False  # mains back; re-arm
        return frag

    def _fire(self, reason: str) -> None:
        log.warning("SHUTDOWN CRITERIA MET: %s", reason)
        # Hand the UPS its shutoff + restart delays, like ups_pdc.js shutoff().
        try:
            self.driver.set("UPS.PowerSummary.DelayBeforeShutdown", self.cfg.shutdown_duration)
            self.driver.set("UPS.PowerSummary.DelayBeforeStartup", self.cfg.shutdown_duration + 10)
        except Exception as e:  # noqa: BLE001
            log.error("failed to arm UPS delays: %s", e)

        if not self.cfg.execute or self.cfg.shutdown_type == "none":
            log.warning("shutdownType=none / execute=False -> NOT powering off host (policy fired only)")
            return
        self._execute_host_action()

    def _execute_host_action(self) -> None:
        t = self.cfg.shutdown_type
        try:
            if t == "script" and self.cfg.shutdown_script:
                subprocess.Popen(self.cfg.shutdown_script, shell=True)  # noqa: S602
            elif sys.platform.startswith("win"):
                subprocess.Popen(["shutdown", "/s" if t == "shutdown" else "/h", "/t", "0"])
            else:
                subprocess.Popen(["systemctl", "hibernate" if t == "hibernate" else "poweroff"])
        except Exception as e:  # noqa: BLE001
            log.error("host shutdown action failed: %s", e)
