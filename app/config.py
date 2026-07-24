from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(x.strip().upper() for x in raw.split(',') if x.strip())


@dataclass(frozen=True)
class Settings:
    app_version: str = os.getenv('APP_VERSION', '1.0.0')
    database_url: str = os.getenv('DATABASE_URL', '')
    supabase_url: str = os.getenv('SUPABASE_URL', '').rstrip('/')
    supabase_service_role_key: str = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
    admin_password: str = os.getenv('ADMIN_PASSWORD', '')
    temp_data_dir: Path = Path(os.getenv('TEMP_DATA_DIR', '/tmp/binance10'))
    quote_assets: tuple[str, ...] = _csv('QUOTE_ASSETS', 'USDT,USDC,FDUSD')
    public_api_base: str = os.getenv('BINANCE_PUBLIC_API_BASE', 'https://data-api.binance.vision').rstrip('/')
    archive_base: str = os.getenv('BINANCE_ARCHIVE_BASE', 'https://data.binance.vision').rstrip('/')
    request_timeout_seconds: int = int(os.getenv('REQUEST_TIMEOUT_SECONDS', '45'))
    request_pause_seconds: float = float(os.getenv('REQUEST_PAUSE_SECONDS', '0.08'))
    worker_poll_seconds: int = int(os.getenv('WORKER_POLL_SECONDS', '8'))
    max_symbols: int = int(os.getenv('MAX_SYMBOLS', '0'))
    storage_bucket: str = os.getenv('STORAGE_BUCKET', 'binance10-research')

    @property
    def configured(self) -> bool:
        return bool(self.database_url)


def get_settings() -> Settings:
    settings = Settings()
    settings.temp_data_dir.mkdir(parents=True, exist_ok=True)
    return settings
