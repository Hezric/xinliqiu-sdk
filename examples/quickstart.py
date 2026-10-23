#!/usr/bin/env python3
"""Minimal end-to-end example: status, inventory, sync, decrypt to WAV.

Set the environment variables listed in README.md, then:

    python examples/quickstart.py
"""

from __future__ import annotations

import os
from pathlib import Path

from aipin_sdk import (
    Credentials,
    Xinliqiu,
    decrypt_enpt,
    parse_cmpt_file,
    to_wav,
)


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing environment variable: {name}")
    return value


def main() -> None:
    ball = Xinliqiu(
        Credentials(
            device_id=env("XINLIQIU_DEVICE_ID"),
            peer_id_file=Path(env("XINLIQIU_PEER_ID_FILE")),
            key_file=Path(env("XINLIQIU_KEY_FILE")),
        )
    )

    print("== capabilities ==")
    caps = ball.capabilities()
    for status in ("supported", "bounded", "unavailable"):
        names = [c["name"] for c in caps["statuses"][status]]
        print(f"  {status:12}: {', '.join(names) or '-'}")

    print("== status ==")
    print(" ", ball.status())

    print("== inventory ==")
    print(" ", ball.inventory_totals())

    start = int(os.environ.get("XINLIQIU_SYNC_START_MS", "0"))
    end = int(os.environ.get("XINLIQIU_SYNC_END_MS", str(1_800_000_000_000)))
    print(f"== sync {start}..{end} ==")
    result = ball.sync_range(start, end)
    out_dir = Path(result["_output_dir"])
    print("  files completed:", result.get("syncFilesCompleted"))
    print("  output:", out_dir)

    owner = os.environ.get("XINLIQIU_OWNER_KEY")
    if not owner:
        print("set XINLIQIU_OWNER_KEY to also decode audio to WAV")
        return

    for path in sorted(out_dir.glob("*.bin")):
        try:
            container = parse_cmpt_file(path)
        except Exception:  # noqa: BLE001 - diagnostics that are not CMPT
            continue
        for entry in container.by_kind("audio"):
            plaintext = decrypt_enpt(entry.payload, Path(owner))
            wav = to_wav(plaintext)
            target = out_dir / f"{entry.name}.wav"
            target.write_bytes(wav)
            print(f"  decoded {entry.name} -> {target.name} ({len(wav)} bytes)")


if __name__ == "__main__":
    main()
