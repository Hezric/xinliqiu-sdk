# -*- coding: utf-8 -*-
"""Command line interface.

Examples::

    xinliqiu capabilities
    xinliqiu status
    xinliqiu inventory
    xinliqiu record start
    xinliqiu record stop
    xinliqiu voiceprint 30 --out ./captures
    xinliqiu sync 1792748000000 1792750200000 --out ./captures
    xinliqiu sync 1792748000000 1792750200000 --store cloudflare

Credentials come from flags or environment:

    XINLIQIU_DEVICE_ID
    XINLIQIU_PEER_ID_FILE
    XINLIQIU_KEY_FILE
    XINLIQIU_AGENT_PUBLIC_KEY   (optional)
    XINLIQIU_OWNER_KEY          (only needed to decrypt / export audio)
    XINLIQIU_STORE_URL / XINLIQIU_STORE_TOKEN  (Cloudflare store)
    XINLIQIU_PROBE              (path to ble-probe binary)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from .client import Credentials, Xinliqiu, scan
from .provision import (
    pair_device,
    default_owner_key_path,
    doctor,
    generate_owner_key,
    pair,
    verify_owner_key,
)
from .userconfig import UserConfig, config_path, resolve
from .container import parse_cmpt_file
from .storage import CloudflareStorage, LocalStorage, ingest_container


class CliError(Exception):
    """A user-facing failure: ``main`` prints it and returns exit code 1."""


def _expand(value: str | None) -> str | None:
    """Expand ``~`` in a user-supplied path."""
    return str(Path(value).expanduser()) if value else None


def _resolve(args: argparse.Namespace):
    resolved = resolve(
        device=getattr(args, "device", None),
        peer_id_file=getattr(args, "peer_id_file", None),
        key_file=getattr(args, "key_file", None),
        owner_key=getattr(args, "owner_key", None),
        agent_public_key=getattr(args, "agent_public_key", None),
    )
    # Paths from flags, environment, and the saved config may all contain "~".
    # Expand them once here so every consumer gets a real path; Credentials
    # expands its own fields, but the owner/agent keys are used directly.
    for field in ("peer_id_file", "key_file", "owner_key", "agent_public_key"):
        value = getattr(resolved, field)
        if value:
            setattr(resolved, field, str(Path(value).expanduser()))
    return resolved


def _credentials(args: argparse.Namespace) -> Credentials:
    resolved = _resolve(args)
    if not resolved.device:
        raise CliError(
            "No device selected.\n"
            "  1. xinliqiu scan                            find your device id\n"
            "  2. xinliqiu pair --device <id> --out-dir ./xinliqiu-creds\n"
            "     writes credentials and remembers them for later commands"
        )
    if not resolved.peer_id_file or not resolved.key_file:
        raise CliError(
            "Missing credentials for device %s.\n"
            "  Run: xinliqiu pair --device %s --out-dir ./xinliqiu-creds\n"
            "  Or pass --peer-id-file and --key-file explicitly."
            % (resolved.device, resolved.device)
        )
    return Credentials(
        device_id=resolved.device,
        peer_id_file=Path(resolved.peer_id_file).expanduser(),
        key_file=Path(resolved.key_file).expanduser(),
    )


def _client(args: argparse.Namespace) -> Xinliqiu:
    resolved = _resolve(args)
    return Xinliqiu(
        _credentials(args),
        agent_public_key=(
            Path(resolved.agent_public_key).expanduser()
            if resolved.agent_public_key
            else None
        ),
        owner_private_key=(
            Path(resolved.owner_key).expanduser() if resolved.owner_key else None
        ),
    )


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_capabilities(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        _print(Xinliqiu.capabilities())
        return 0

    caps = Xinliqiu.capabilities()
    statuses = caps["statuses"]
    print()
    print("  " + caps["device"])
    print()
    labels = (
        ("supported", "可用"),
        ("bounded", "可用但有边界"),
        ("unavailable", "不可用 - 不要基于这些做设计"),
    )
    for key, label in labels:
        items = statuses.get(key) or []
        if not items:
            continue
        print("  " + label)
        print("  " + "-" * 58)
        for item in items:
            print("    " + item["name"])
            for chunk in _wrap(item["note"], 52):
                print("        " + chunk)
        print()
    print("  Tip: pass --json for machine-readable output.")
    print()
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word) if current else word
    if current:
        lines.append(current)
    return lines or [""]


def cmd_status(args: argparse.Namespace) -> int:
    _print(_client(args).status().__dict__)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Format the device and install the owner public key.

    This is the only step that decides who can read the recordings, and the
    device lets it happen once. It also rewrites the account record, so
    anything already on the device is gone - hence the explicit gate.
    """
    if not args.allow_format:
        raise CliError(
            "init erases everything the device currently holds and can only be "
            "done once per device; re-run with --allow-format if that is what "
            "you want"
        )
    ball = _client(args)
    result = ball.init_device(
        owner_public_key=Path(args.owner_public_key).expanduser(),
        signing_key=Path(args.signing_key).expanduser(),
        account=args.account,
        public_key_version=args.public_key_version,
        signing_key_is_java_source=args.signing_key_is_java_source,
    )
    _print(result)
    return 0


def cmd_storage(args: argparse.Namespace) -> int:
    _print(_client(args).storage_info())
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    _print(_client(args).inventory_totals(args.start_ms, args.end_ms))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    ball = _client(args)
    result = ball.record_start() if args.action == "start" else ball.record_stop()
    _print({"action": args.action, **result})
    return 0


def cmd_voiceprint(args: argparse.Namespace) -> int:
    result = _client(args).collect_voiceprint(args.seconds)
    _print({k: v for k, v in result.items() if k in ("_output_dir", "reason") or not isinstance(v, str)})
    return 0


def _build_storage(args: argparse.Namespace):
    """Validate the store configuration before any BLE work starts."""
    store_name = args.store
    if not store_name:
        return None
    if store_name == "local":
        return LocalStorage(Path(args.store_path or "./xinliqiu-store").expanduser())
    if store_name == "cloudflare":
        url = os.environ.get("XINLIQIU_STORE_URL")
        token = os.environ.get("XINLIQIU_STORE_TOKEN")
        if not url or not token:
            raise CliError(
                "XINLIQIU_STORE_URL and XINLIQIU_STORE_TOKEN are required"
            )
        return CloudflareStorage(url, token)
    raise CliError(f"unknown store: {store_name}")


def cmd_sync(args: argparse.Namespace) -> int:
    ball = _client(args)
    # Build the store first: a bad backend or missing token must not waste the
    # multi-minute BLE transfer that sync_range performs.
    storage = _build_storage(args)
    out_dir = Path(args.out).expanduser() if args.out else None
    result = ball.sync_range(args.start_ms, args.end_ms, out_dir)
    summary = {
        "output_dir": result.get("_output_dir"),
        "files": result.get("files") or [],
        "count": result.get("count") or 0,
        "bytes": result.get("bytes") or 0,
        "offers": result.get("offers") or 0,
        "rejected": result.get("rejected") or [],
    }
    if storage is not None:
        # Device-supplied names can have any suffix; use the files list the
        # sync returned instead of guessing at "*.bin".
        device_id = None
        totals = {"stored": 0, "duplicates": 0, "total": 0}
        for file_name in summary["files"]:
            path = Path(file_name)
            try:
                container = parse_cmpt_file(path)
            except Exception as exc:  # noqa: BLE001 - report, never skip silently
                raise CliError(f"cannot parse synced file {path}: {exc}") from exc
            if not device_id:
                try:
                    device_id = _credentials(args).device_id
                except CliError:
                    device_id = getattr(
                        getattr(ball, "credentials", None), "device_id", None
                    )
                    if not device_id:
                        raise
            counts = ingest_container(container, storage, device_id)
            for key in totals:
                totals[key] += counts[key]
        summary["store"] = {"backend": args.store, **totals}
    _print(summary)
    return 0 if summary["count"] and not summary["rejected"] else 1



def cmd_scan(args: argparse.Namespace) -> int:
    found = scan(args.name, args.seconds)
    _print({"devices": found, "count": len(found)})
    return 0 if found else 1


def cmd_keygen(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else None
    info = generate_owner_key(out_dir, overwrite=args.force)
    _print(info)
    print(file=sys.stderr)
    print("  Back this file up NOW, outside this machine.", file=sys.stderr)
    print("  If it is lost, every recording encrypted to it is unreadable forever.", file=sys.stderr)
    return 0


def cmd_keycheck(args: argparse.Namespace) -> int:
    key = (
        Path(args.owner_key).expanduser()
        if args.owner_key
        else default_owner_key_path()
    )
    sample = None
    if args.sample:
        from .container import parse_cmpt_file

        sample_path = Path(args.sample).expanduser()
        if sample_path.is_file():
            candidates = [sample_path]
        elif sample_path.is_dir():
            candidates = sorted(p for p in sample_path.iterdir() if p.is_file())
        else:
            candidates = []
        for path in candidates:
            try:
                container = parse_cmpt_file(path)
            except Exception:  # noqa: BLE001 - not every captured file is CMPT
                continue
            entries = container.by_kind("audio")
            if entries:
                sample = entries[0].payload
                break
    result = verify_owner_key(key, sample)
    if args.sample:
        result["sample_used"] = sample is not None
    _print(result)
    if args.sample and sample is None:
        # The key may be fine, but the requested check could not run.
        return 1
    return 0 if result.get("ok") else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    result = doctor(
        args.device or os.environ.get("XINLIQIU_DEVICE_ID", ""),
        _expand(args.peer_id_file or os.environ.get("XINLIQIU_PEER_ID_FILE")),
        _expand(args.key_file or os.environ.get("XINLIQIU_KEY_FILE")),
        _expand(args.owner_key or os.environ.get("XINLIQIU_OWNER_KEY")),
    )
    _print(result)
    return 0 if result["ready"] else 1


def cmd_pair(args: argparse.Namespace) -> int:
    if not args.device:
        raise CliError("pair requires --device <id>  (run: xinliqiu scan)")
    # Reuse the alias this host first paired under, if any. The device updates a
    # matching slot in place instead of evicting the least-recently-used one, so
    # a re-pair cannot push the user's phone out of its slot.
    prior = UserConfig.load()
    alias = prior.ksc_alias or secrets.token_hex(6)

    result = pair_device(
        args.device, Path(args.out_dir).expanduser(), ksc_alias=alias
    )

    # Remember the paths so later commands need no flags at all.
    saved = UserConfig.load().merged(
        device=args.device,
        peer_id_file=str(Path(result.peer_id_file).expanduser()),
        key_file=str(Path(result.key_file).expanduser()),
        ksc_alias=alias,
    )
    saved.save()

    _print({
        "paired": True,
        "key_file": str(result.key_file),
        "peer_id_file": str(result.peer_id_file),
        "remembered_in": str(config_path()),
        "next": "run: xinliqiu status",
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Credential flags are accepted both before and after the subcommand so that
    # `xinliqiu --device X status` and `xinliqiu status --device X` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--device", help="CoreBluetooth UUID")
    common.add_argument("--peer-id-file")
    common.add_argument("--key-file")
    common.add_argument("--agent-public-key")
    common.add_argument("--owner-key")

    parser = argparse.ArgumentParser(prog="xinliqiu", description="心力球 SDK CLI")
    parser.add_argument("--device", help="CoreBluetooth UUID")
    parser.add_argument("--peer-id-file")
    parser.add_argument("--key-file")
    parser.add_argument("--agent-public-key")
    parser.add_argument("--owner-key")
    sub = parser.add_subparsers(dest="command", required=True)

    caps = sub.add_parser("capabilities", parents=[common], help="show what the device supports")
    caps.add_argument("--json", action="store_true", help="raw JSON instead of a table")

    sub.add_parser("status", parents=[common], help="read recording state, battery, error byte")

    sc = sub.add_parser("scan", help="discover nearby devices")
    sc.add_argument("--name", help="filter by advertised name substring")
    sc.add_argument("--seconds", type=int, default=15)

    kg = sub.add_parser("keygen", help="create an owner key pair")
    kg.add_argument("--out-dir")
    kg.add_argument("--force", action="store_true", help="replace an existing key")

    kc = sub.add_parser("keycheck", parents=[common], help="verify the owner key")
    kc.add_argument("--sample", help="directory of synced containers to test against")

    doc = sub.add_parser("doctor", parents=[common], help="check local prerequisites")

    pr = sub.add_parser("pair", parents=[common], help="pair with a fresh device and write credentials")
    pr.add_argument("--out-dir", required=True)

    ini = sub.add_parser(
        "init",
        parents=[common],
        help="format the device and install an owner public key (once per device)",
    )
    ini.add_argument("--owner-public-key", required=True, help="base64 SPKI text file")
    ini.add_argument("--signing-key", required=True, help="P-256 private key that the device trusts")
    ini.add_argument(
        "--signing-key-is-java-source",
        action="store_true",
        help="the signing key is the APK's K1BindingSigner Java source, not a key file",
    )
    ini.add_argument("--account", default="local-owner")
    ini.add_argument("--public-key-version", type=int, default=1)
    ini.add_argument(
        "--allow-format",
        action="store_true",
        help="acknowledge that this erases everything the device currently holds",
    )

    sto = sub.add_parser("storage", parents=[common], help="usage and low-space thresholds")

    inv = sub.add_parser("inventory", parents=[common], help="aggregate counts for a device-clock range")
    inv.add_argument("--start-ms", type=int, default=0)
    inv.add_argument("--end-ms", type=int, default=9_999_999_999_999)

    rec = sub.add_parser("record", parents=[common], help="start or stop recording")
    rec.add_argument("action", choices=["start", "stop"])

    vp = sub.add_parser("voiceprint", parents=[common], help="single voiceprint capture (1..127 s)")
    vp.add_argument("seconds", type=int, nargs="?", default=6)

    syn = sub.add_parser("sync", parents=[common], help="fetch a device-clock range")
    syn.add_argument("start_ms", type=int)
    syn.add_argument("end_ms", type=int)
    syn.add_argument("--out")
    syn.add_argument("--store", choices=["local", "cloudflare"])
    syn.add_argument("--store-path", help="path for --store local")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "capabilities": cmd_capabilities,
        "status": cmd_status,
        "storage": cmd_storage,
        "inventory": cmd_inventory,
        "record": cmd_record,
        "voiceprint": cmd_voiceprint,
        "sync": cmd_sync,
        "scan": cmd_scan,
        "keygen": cmd_keygen,
        "keycheck": cmd_keycheck,
        "doctor": cmd_doctor,
        "pair": cmd_pair,
        "init": cmd_init,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
