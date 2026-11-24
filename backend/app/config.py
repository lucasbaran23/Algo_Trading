"""Runtime configuration, read from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


ENV_INFO: dict = {"path": None, "note": ""}


def _load_dotenv() -> None:
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if not env_path.exists():
        # Windows Notepad often saves ".env" as ".env.txt"
        alt = next((p for p in (root / ".env.txt", root / "backend" / ".env") if p.exists()), None)
        if alt is None:
            ENV_INFO["note"] = f"no .env file found in {root}"
            return
        ENV_INFO["note"] = f"using {alt.name} (expected .env in {root})"
        env_path = alt
    ENV_INFO["path"] = str(env_path)
    for line in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()   # drop inline comments
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # "sim" = fully simulated, deterministic data (works offline, good for demos).
    # "live" = try real sources (CFTC, EIA, Yahoo) and fall back to sim per-source on failure.
    data_mode: str = field(default_factory=lambda: os.getenv("DATA_MODE", "sim").lower())
    eia_api_key: str = field(default_factory=lambda: os.getenv("EIA_API_KEY", ""))
    tick_interval_s: float = field(default_factory=lambda: float(os.getenv("TICK_INTERVAL_S", "1.0")))
    signal_interval_s: float = field(default_factory=lambda: float(os.getenv("SIGNAL_INTERVAL_S", "15")))
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("CACHE_DIR", Path(__file__).resolve().parents[1] / ".cache"))
    )
    sim_seed: int = field(default_factory=lambda: int(os.getenv("SIM_SEED", "42")))

    # Broker / prop-firm connectivity. Live order routing is OFF unless explicitly enabled.
    live_trading_enabled: bool = field(default_factory=lambda: _bool("LIVE_TRADING_ENABLED"))
    broker: str = field(default_factory=lambda: os.getenv("BROKER", "paper").lower())
    tradovate_env: str = field(default_factory=lambda: os.getenv("TRADOVATE_ENV", "demo"))
    tradovate_user: str = field(default_factory=lambda: os.getenv("TRADOVATE_USER", ""))
    tradovate_password: str = field(default_factory=lambda: os.getenv("TRADOVATE_PASSWORD", ""))
    tradovate_app_id: str = field(default_factory=lambda: os.getenv("TRADOVATE_APP_ID", ""))
    tradovate_cid: str = field(default_factory=lambda: os.getenv("TRADOVATE_CID", ""))
    tradovate_secret: str = field(default_factory=lambda: os.getenv("TRADOVATE_SECRET", ""))
    projectx_base_url: str = field(default_factory=lambda: os.getenv("PROJECTX_BASE_URL", ""))
    projectx_user: str = field(default_factory=lambda: os.getenv("PROJECTX_USER", ""))
    projectx_api_key: str = field(default_factory=lambda: os.getenv("PROJECTX_API_KEY", ""))

    @property
    def live(self) -> bool:
        return self.data_mode == "live"


settings = Settings()
