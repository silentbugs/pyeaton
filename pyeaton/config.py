"""
Persistent configuration, mirroring the structure of the recovered
configs/config.js (`cfg = {...}`). Stored as JSON so it round-trips cleanly.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("config")


@dataclass
class ShutdownSettings:
    powerSource: str = "sim"
    loadSegment: int = 1
    shutdownDuration: int = 120
    shutdownTimer: int = -1
    runTimeToEmptyLimit: int = 300
    shutoffControl: int = 1
    shutdownType: str = "none"   # none|hibernate|shutdown|script
    shutdownScript: str = ""
    execute: bool = False        # master safety switch (not in original; safety)


@dataclass
class EnergySettings:
    resetPeriod: str = "weekly"  # never|daily|weekly|monthly
    cost: float = 0.0
    currency: str = "-"
    powerFactor: float = 0.8
    consumption: float = 0.0     # accumulated watt-seconds
    resetDate: float = 0.0


@dataclass
class Acquisition:
    initDispatch: int = 100
    pollingRate: int = 2000


@dataclass
class Config:
    http_port: int = 4679
    http_host: str = "0.0.0.0"
    nut_enabled: bool = False
    nut_port: int = 3493
    nut_name: str = "ups"
    api_token: str = ""          # if set, /api/* and settings require it
    acquisition: Acquisition = field(default_factory=Acquisition)
    shutdownSettings: ShutdownSettings = field(default_factory=ShutdownSettings)
    energy: EnergySettings = field(default_factory=EnergySettings)

    _path: Path | None = field(default=None, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # --- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path:
            cfg._path = Path(path)
            if cfg._path.exists():
                try:
                    cfg.apply(json.loads(cfg._path.read_text()))
                except Exception as e:  # noqa: BLE001
                    log.error("failed to read %s: %s", path, e)
        return cfg

    def apply(self, d: dict) -> None:
        """Merge a (possibly partial) dict into the config."""
        for k, v in d.items():
            if k.startswith("_"):
                continue
            cur = getattr(self, k, None)
            if isinstance(cur, (ShutdownSettings, EnergySettings, Acquisition)) and isinstance(v, dict):
                for kk, vv in v.items():
                    if hasattr(cur, kk):
                        setattr(cur, kk, vv)
            elif hasattr(self, k):
                setattr(self, k, v)

    def to_dict(self) -> dict:
        # Build manually: asdict() would recurse into the threading.Lock field
        # and fail to deep-copy it.
        return {
            "http_host": self.http_host,
            "http_port": self.http_port,
            "nut_enabled": self.nut_enabled,
            "nut_port": self.nut_port,
            "nut_name": self.nut_name,
            "api_token": self.api_token,
            "acquisition": asdict(self.acquisition),
            "shutdownSettings": asdict(self.shutdownSettings),
            "energy": asdict(self.energy),
        }

    def save(self) -> None:
        if not self._path:
            return
        with self._lock:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2))
            tmp.replace(self._path)
