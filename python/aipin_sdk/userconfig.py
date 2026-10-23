# -*- coding: utf-8 -*-
"""Remember where credentials live so users stop retyping paths.

After `pair`, the credential paths are saved to a small config file. Every
later command picks them up automatically, which turns the common case from

    xinliqiu sync 0 1800000000000 \\
      --device X --peer-id-file ./creds/peer-id.txt --key-file ./creds/key.json

into

    xinliqiu sync 0 1800000000000

Precedence, highest first:

    command line flag  >  environment variable  >  saved config

The config holds **paths only** - never credential contents, never keys.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_ENV = "XINLIQIU_CONFIG"


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "xinliqiu" / "config.json"


@dataclass
class UserConfig:
    device: str | None = None
    peer_id_file: str | None = None
    key_file: str | None = None
    owner_key: str | None = None
    agent_public_key: str | None = None
    store: str | None = None
    store_url: str | None = None
    ksc_alias: str | None = None

    @classmethod
    def load(cls) -> "UserConfig":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a broken config must never break the CLI
            return cls()
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: getattr(self, k) for k in self.__dataclass_fields__ if getattr(self, k)}
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.chmod(path, 0o600)
        return path

    def merged(self, **overrides: str | None) -> "UserConfig":
        """Return a copy where explicit values win over saved ones."""
        merged = UserConfig(**{k: getattr(self, k) for k in self.__dataclass_fields__})
        for key, value in overrides.items():
            if value:
                setattr(merged, key, value)
        return merged


def resolve(*, device: str | None, peer_id_file: str | None, key_file: str | None,
            owner_key: str | None = None, agent_public_key: str | None = None):
    """Resolve credentials from flags, then environment, then saved config."""
    saved = UserConfig.load()
    return saved.merged(
        device=device or os.environ.get("XINLIQIU_DEVICE_ID"),
        peer_id_file=peer_id_file or os.environ.get("XINLIQIU_PEER_ID_FILE"),
        key_file=key_file or os.environ.get("XINLIQIU_KEY_FILE"),
        owner_key=owner_key or os.environ.get("XINLIQIU_OWNER_KEY"),
        agent_public_key=agent_public_key or os.environ.get("XINLIQIU_AGENT_PUBLIC_KEY"),
    )
