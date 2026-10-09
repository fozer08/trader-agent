from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env(path: Path = Path(".env")) -> None:
    """Seçilen .env dosyasını okur; göreli dizinleri dosyanın konumuna göre çözer.

    Varsayılan dosya çalışma dizinindeki .env'dir. Mevcut ortam değerleri
    önceliklidir; dosya yoksa yalnız ortam değişkenleri kullanılır.
    """
    path = path.expanduser().resolve()
    load_dotenv(path, override=False)
    for name in ("TRADER_CONFIGS_DIR", "TRADER_DATA_DIR", "TRADER_LOGS_DIR"):
        value = os.environ.get(name)
        if value:
            os.environ[name] = str((path.parent / Path(value).expanduser()).resolve())


def require_path_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} zorunlu. Bu dizini .env veya ortam degiskeni ile tanimlayin."
        )
    return Path(value).expanduser()
