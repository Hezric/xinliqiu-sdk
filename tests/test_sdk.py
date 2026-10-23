# -*- coding: utf-8 -*-
"""Offline test suite. No device, no network, no keys."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import aipin_sdk as hb
from aipin_sdk import oggopus
from aipin_sdk.mcp_tools import build_server


def make_cmpt(entries: list[tuple[str, bytes]]) -> bytes:
    table = bytearray()
    offset = 12 + len(entries) * 68
    for name, payload in entries:
        raw = name.encode("utf-8")[:64].ljust(64, b"\x00")
        table += raw + struct.pack("<I", len(payload))
    body = b"".join(p for _, p in entries)
    if offset % 4:
        pad = 4 - offset % 4
        offset += pad
        table += b"\x00" * pad
    declared = offset + len(body)
    return b"CMPT" + struct.pack("<HHI", len(entries), offset, declared) + bytes(table) + body


def make_enpt(payload: bytes = b"x" * 40) -> bytes:
    head = b"enpt" + struct.pack("<HH", 256, 12) + b"\x00" * 256 + b"\x00" * 12
    head += struct.pack("<H", 0x0118) + bytes([1, 0])
    trailer = b"\x00" * 16 + struct.pack("<HH", 16, 24) + b"enpt"
    return head + payload + trailer


def test_capabilities_shape():
    caps = hb.Xinliqiu.capabilities()
    assert "CNMABWC301" in caps["device"]
    unavailable = {c["name"] for c in caps["statuses"]["unavailable"]}
    assert "mark.add" in unavailable
    assert all(c["note"] for c in caps["statuses"]["unavailable"])
    print("ok capabilities")


def test_require_capability_blocks_unavailable():
    try:
        hb.Xinliqiu.require_capability("mark.add")
    except hb.Unsupported:
        pass
    else:
        raise AssertionError("mark.add should be refused")
    hb.Xinliqiu.require_capability("record.status")
    print("ok require_capability")


def test_parse_cmpt_roundtrip():
    audio = make_enpt(b"a" * 80)
    marker = make_enpt(b"m" * 40)
    data = make_cmpt([("1000-2000.enc.opus", audio), ("3000-3100.end.enc.opus", marker)])
    container = hb.parse_cmpt(data)
    assert len(container.entries) == 2
    assert container.entries[0].kind == "audio"
    assert container.entries[1].kind == "end-marker"
    assert container.entries[0].size == len(audio)
    assert len(container.by_kind("audio")) == 1
    print("ok parse_cmpt")


def test_parse_cmpt_rejects_bad_magic():
    try:
        hb.parse_cmpt(b"XXXX" + b"\x00" * 20)
    except hb.ContainerError:
        pass
    else:
        raise AssertionError("bad magic accepted")
    print("ok parse_cmpt rejects bad magic")


def test_parse_enpt_fields():
    info = hb.parse_enpt(make_enpt(b"z" * 40))
    assert info.wrapped_key_length == 256
    assert info.nonce_length == 12
    assert info.public_key_version == 1
    assert info.plaintext_length == 40
    assert len(info.tag) == 16
    print("ok parse_enpt")


def test_opus_layout():
    layout = hb.OpusLayout()
    payload = b"\x00" * 200
    assert layout.packet_count(payload) == 5
    assert abs(layout.duration_seconds(payload) - 0.1) < 1e-9
    try:
        layout.packet_count(b"\x00" * 41)
    except hb.ContainerError:
        pass
    else:
        raise AssertionError("misaligned payload accepted")
    print("ok opus layout")


def test_ogg_muxer():
    ogg = oggopus.encode(b"\x00" * 120, packet_bytes=40)
    assert ogg.startswith(b"OggS")
    assert b"OpusHead" in ogg
    assert b"OpusTags" in ogg
    try:
        oggopus.encode(b"\x00" * 41, packet_bytes=40)
    except ValueError:
        pass
    else:
        raise AssertionError("misaligned payload accepted by muxer")
    print("ok ogg muxer")


def test_local_storage_roundtrip(tmp: Path):
    storage = hb.LocalStorage(tmp / "store")
    entry = hb.parse_cmpt(make_cmpt([("100-200.enc.opus", make_enpt(b"q" * 40))])).entries[0]
    first = storage.put_entry("dev1", entry)
    assert first["status"] == "stored"
    second = storage.put_entry("dev1", entry)
    assert second["status"] == "duplicate"
    listed = storage.list("dev1", 0, 10_000)
    assert len(listed) == 1
    assert listed[0].kind == "audio"
    assert storage.get(first["object_key"]) == entry.payload
    print("ok local storage")


def test_mcp_protocol(tmp: Path):
    server = build_server()
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "xinliqiu"

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in listed["result"]["tools"]}
    expected = {
        "xinliqiu_capabilities",
        "xinliqiu_status",
        "xinliqiu_inventory", "xinliqiu_storage",
        "xinliqiu_record_start",
        "xinliqiu_record_stop",
        "xinliqiu_collect_voiceprint",
        "xinliqiu_sync",
        "xinliqiu_task_status",
        "xinliqiu_fetch_audio",
        "xinliqiu_pull_changes",
        "xinliqiu_digest",
    }
    assert names == expected, names

    called = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "xinliqiu_capabilities", "arguments": {}},
        }
    )
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert any(x["name"] == "mark.add" for x in payload["statuses"]["unavailable"])

    missing = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        }
    )
    assert missing["error"]["code"] == -32602
    print("ok mcp protocol")


def test_mcp_stdio():
    proc = subprocess.Popen(
        [str(ROOT / "mcp" / "run.sh")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        response = json.loads(line)
        assert response["result"]["serverInfo"]["name"] == "xinliqiu"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print("ok mcp stdio launcher")



def test_sync_helpers(tmp: Path):
    """pull_changes must work against a fake incremental store."""
    from aipin_sdk.sync import pull_changes as pc

    class FakeStore:
        def __init__(self):
            self.data = {}
            self.seq = 0
            self.blobs = {}

        def put_entry(self, device_id, entry):
            self.seq += 1
            key = f"{device_id}/{entry.index}"
            self.data[key] = hb.StoredObject(
                object_key=key, entry_name=entry.name, kind=entry.kind,
                size_bytes=len(entry.payload), sha256="0" * 64, start_ms=None, end_ms=None,
            )
            self.blobs[key] = entry.payload
            return {"status": "stored", "seq": self.seq}

        def changes(self, device_id, since=0, limit=500):
            items = list(self.data.values())[since:since + limit]
            return {"objects": items, "cursor": since + len(items), "has_more": False}

        def get(self, key):
            return self.blobs[key]

    store = FakeStore()
    container = hb.parse_cmpt(make_cmpt([("1-2.enc.opus", make_enpt(b"a" * 40))]))
    hb.ingest_container(container, store, "dev")
    result = pc(store, "dev", since=0)
    assert result["count"] == 1
    assert result["objects"][0]["kind"] == "audio"
    print("ok pull_changes")



def test_doctor_reports_missing(tmp: Path):
    result = hb.doctor("dev-1", None, None)
    assert result["ready"] is False
    names = {c["check"] for c in result["checks"]}
    assert {"device_id", "peer_id_file", "key_file"} <= names
    print("ok doctor reports missing")


def test_doctor_ready_with_files(tmp: Path):
    peer = tmp / "peer-id.txt"
    peer.write_text(hb.generate_peer_id(), encoding="utf-8")
    key = tmp / "key.json"
    key.write_text(json.dumps({"k": 1}), encoding="utf-8")
    result = hb.doctor("dev-1", peer, key)
    assert result["ready"] is True, result
    print("ok doctor ready")


def test_peer_id_shape():
    pid = hb.generate_peer_id()
    assert len(pid) == 36
    assert all(c in "0123456789abcdef" for c in pid)
    assert hb.generate_peer_id() != pid
    print("ok peer id shape")


def test_pair_refuses_overwrite(tmp: Path):
    out = tmp / "creds"
    out.mkdir()
    (out / "key.json").write_text("{}", encoding="utf-8")
    try:
        hb.pair("dev-1", out)
    except hb.XinliqiuError as exc:
        assert "already exist" in str(exc)
    else:
        raise AssertionError("pair overwrote existing credentials")
    print("ok pair refuses overwrite")


def test_install_owner_key_requires_ack(tmp: Path):
    try:
        hb.install_owner_key("dev", tmp / "p", tmp / "k", tmp / "pub", tmp / "sig", tmp)
    except hb.XinliqiuError as exc:
        assert "acknowledge_risk" in str(exc)
    else:
        raise AssertionError("install_owner_key ran without acknowledgement")
    print("ok install_owner_key requires ack")


def test_scan_aggregates_events():
    class FakeTransport:
        def run(self, argv, timeout):
            return [
                {"event": "advertisement", "id": "A", "cachedName": "dev-a", "rssi": -80, "connectable": 1},
                {"event": "advertisement", "id": "A", "cachedName": "dev-a", "rssi": -60, "connectable": 1},
                {"event": "advertisement", "id": "B", "cachedName": "dev-b", "rssi": -70, "connectable": 1},
                {"event": "finished", "exitCode": 0},
            ]
    found = hb.scan(transport=FakeTransport())
    assert len(found) == 2
    assert found[0]["id"] == "A"          # strongest first
    assert found[0]["rssi"] == -60        # best observation kept
    print("ok scan aggregation")



def test_sync_rejects_future_end(tmp: Path):
    import time
    from aipin_sdk.client import Transport

    class Noop(Transport):
        def run(self, argv, timeout):
            return [{"event": "finished", "exitCode": 0}]

    ball = hb.Xinliqiu(_creds(tmp), transport=Noop())
    future = int(time.time() * 1000) + 3_600_000
    try:
        ball.sync_range(0, future)
    except ValueError as exc:
        assert "future" in str(exc)
    else:
        raise AssertionError("future end was accepted; it would hang silently")
    print("ok sync rejects future end")


def test_sync_rejects_inverted_range(tmp: Path):
    from aipin_sdk.client import Transport

    class Noop(Transport):
        def run(self, argv, timeout):
            return [{"event": "finished", "exitCode": 0}]

    ball = hb.Xinliqiu(_creds(tmp), transport=Noop())
    for start, end in ((100, 50), (-1, 10)):
        try:
            ball.sync_range(start, end)
        except ValueError:
            continue
        raise AssertionError(f"range {start}..{end} was accepted")
    print("ok sync rejects inverted and negative ranges")


def test_sync_dir_is_private(tmp: Path):
    import stat as st
    from aipin_sdk.client import Transport

    seen = {}

    class Noop(Transport):
        def run(self, argv, timeout):
            idx = argv.index("--output-dir")
            seen["dir"] = Path(argv[idx + 1])
            return [{"event": "finished", "exitCode": 0}]

    ball = hb.Xinliqiu(_creds(tmp), transport=Noop())
    ball.sync_range(0, 1_000)
    mode = st.S_IMODE(seen["dir"].stat().st_mode)
    assert mode == 0o700, f"output dir mode {oct(mode)}; the transport rejects group/other bits"
    print("ok sync output dir is 0700")




def test_keygen_creates_private_key(tmp: Path):
    info = hb.generate_owner_key(tmp)
    key = Path(info["private_key"])
    assert key.exists(), "private key not written"
    mode = key.stat().st_mode & 0o777
    assert mode == 0o400, f"private key mode {oct(mode)} is too open"
    assert Path(info["public_key_spki"]).exists()
    assert "Back this file up" in info["warning"]
    print("ok keygen writes a 0400 private key and warns")


def test_keygen_refuses_overwrite(tmp: Path):
    hb.generate_owner_key(tmp)
    try:
        hb.generate_owner_key(tmp)
    except hb.XinliqiuError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("keygen replaced an existing key; that orphans every recording")
    print("ok keygen refuses overwrite")


def test_default_key_path_is_not_cwd():
    path = hb.default_owner_key_path()
    assert path.is_absolute(), f"default key path {path} is not absolute"
    assert Path.cwd() not in path.parents, f"default key path {path} sits inside a working directory"
    assert path.name == "owner.pem"
    print("ok default key path is outside the working directory")


def test_keycheck_reports_missing_and_valid(tmp: Path):
    hb.generate_owner_key(tmp)
    good = hb.verify_owner_key(tmp / "owner.pem")
    assert good["ok"] is True, good
    assert good["bits"] == 2048

    missing = hb.verify_owner_key(tmp / "nope.pem")
    assert missing["ok"] is False and missing["reason"] == "missing"
    print("ok keycheck reports valid and missing keys")


def _creds(tmp: Path):
    peer = tmp / "peer.txt"
    peer.write_text("x" * 36, encoding="utf-8")
    key = tmp / "key.json"
    key.write_text("{}", encoding="utf-8")
    return hb.Credentials("dev", peer, key)


def run() -> int:
    import tempfile

    tests = [
        test_capabilities_shape,
        test_require_capability_blocks_unavailable,
        test_parse_cmpt_roundtrip,
        test_parse_cmpt_rejects_bad_magic,
        test_parse_enpt_fields,
        test_opus_layout,
        test_ogg_muxer,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_local_storage_roundtrip, test_mcp_protocol, test_sync_helpers):
            try:
                fn(Path(tmp))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    for fn in (test_doctor_reports_missing, test_doctor_ready_with_files):
        try:
            with tempfile.TemporaryDirectory() as tmp2:
                fn(Path(tmp2))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    for fn in (test_sync_rejects_future_end, test_sync_rejects_inverted_range, test_sync_dir_is_private):
        try:
            with tempfile.TemporaryDirectory() as tmp4:
                fn(Path(tmp4))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    for fn in (test_keygen_creates_private_key, test_keygen_refuses_overwrite,
               test_keycheck_reports_missing_and_valid):
        try:
            with tempfile.TemporaryDirectory() as kt:
                fn(Path(kt))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    try:
        test_default_key_path_is_not_cwd()
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL test_default_key_path_is_not_cwd: {type(exc).__name__}: {exc}")
    for fn in (test_peer_id_shape, test_scan_aggregates_events):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    with tempfile.TemporaryDirectory() as tmp3:
        for fn in (test_pair_refuses_overwrite, test_install_owner_key_requires_ack):
            try:
                fn(Path(tmp3))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    try:
        test_mcp_stdio()
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL test_mcp_stdio: {type(exc).__name__}: {exc}")

    total = len(tests) + 17
    print(f"\n{total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
