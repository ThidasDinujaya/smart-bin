# settings come from environment variables
import os
from dataclasses import dataclass


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    db_host: str = ""                        # MySQL server (AWS RDS endpoint or localhost)
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "smartbin"                # created automatically if missing
    db_path: str = ""                        # SQLite file, tests only
    api_key: str = "change-me-device-key"     # devices send this as X-API-Key
    admin_token: str = ""                    # needed for operator actions
    protect_dashboard: bool = False          # lock the dashboard behind the token
    default_depth_cm: float = 80.0           # depth for auto-created bins
    dead_zone_cm: float = 10.0               # gap between sensor and the full line
    alert_pct: float = 80.0                  # nearly full level
    full_pct: float = 95.0                   # full level
    hysteresis_pct: float = 10.0             # alert clears below alert minus this
    empty_drop_pct: float = 30.0             # a drop this big means emptied
    stale_after_min: float = 150.0           # silent this long means offline
    command_ttl_sec: int = 60                # refresh expires if nobody answers
    refresh_cooldown_sec: int = 15           # gap between refreshes per bin
    poll_baseline_min: float = 1.0           # compare against polling every N min
    max_readings_per_bin: int = 5000         # readings kept per bin

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        g = os.getenv
        return cls(
            db_host=g("DB_HOST", d.db_host), db_port=int(g("DB_PORT", d.db_port)),
            db_user=g("DB_USER", d.db_user), db_password=g("DB_PASSWORD", d.db_password),
            db_name=g("DB_NAME", d.db_name), db_path=g("DB_PATH", d.db_path),
            api_key=g("API_KEY", d.api_key),
            admin_token=g("ADMIN_TOKEN", d.admin_token),
            protect_dashboard=_bool(g("PROTECT_DASHBOARD", str(d.protect_dashboard))),
            default_depth_cm=float(g("DEFAULT_DEPTH_CM", d.default_depth_cm)),
            dead_zone_cm=float(g("DEAD_ZONE_CM", d.dead_zone_cm)),
            alert_pct=float(g("ALERT_PCT", d.alert_pct)), full_pct=float(g("FULL_PCT", d.full_pct)),
            hysteresis_pct=float(g("HYSTERESIS_PCT", d.hysteresis_pct)),
            empty_drop_pct=float(g("EMPTY_DROP_PCT", d.empty_drop_pct)),
            stale_after_min=float(g("STALE_AFTER_MIN", d.stale_after_min)),
            command_ttl_sec=int(g("COMMAND_TTL_SEC", d.command_ttl_sec)),
            refresh_cooldown_sec=int(g("REFRESH_COOLDOWN_SEC", d.refresh_cooldown_sec)),
            poll_baseline_min=float(g("POLL_BASELINE_MIN", d.poll_baseline_min)),
            max_readings_per_bin=int(g("MAX_READINGS_PER_BIN", d.max_readings_per_bin)),
        )
