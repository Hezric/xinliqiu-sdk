# -*- coding: utf-8 -*-
"""Static capability table for the OPPO AI Xinliqiu (心力球).

This table is the contract between the SDK and its callers.  Every entry was
established by protocol observation against a real device (model CNMABWC301,
firmware CNMABWC301_11.A.06_0061) and is stable across sessions.

`SUPPORTED`       the SDK implements it and it works on the device.
`BOUNDED`         works, but only within the documented limits.
`UNAVAILABLE`     the device behaviour exists internally but no reachable
                  trigger exists on this firmware.  Do not build product
                  flows on these.
"""

SUPPORTED = "supported"
BOUNDED = "bounded"
UNAVAILABLE = "unavailable"

CAPABILITIES = {
    "auth.reuse_existing_ksc": {
        "status": SUPPORTED,
        "note": "Authenticate with an existing KSC/peer-id pair. No pairing or INIT.",
    },
    "record.status": {
        "status": SUPPORTED,
        "note": "Read-only state query: recording state, battery, error byte.",
    },
    "record.start_stop": {
        "status": BOUNDED,
        "note": ("Toggle recording over BLE. A started recording produces no audio "
                 "file unless voice activity is detected."),
    },
    "record.vad_gated_audio": {
        "status": BOUNDED,
        "note": "Audio files are created only when VAD detects speech.",
    },
    "record.segmentation": {
        "status": BOUNDED,
        "note": ("Speech separated by silence produces separate files. Ordinary "
                 "file rollover happens at 120,000 bytes (~60 s)."),
    },
    "record.silence_retention": {
        "status": BOUNDED,
        "note": "Trailing silence is retained for a bounded period, not indefinitely.",
    },
    "voiceprint.collect": {
        "status": BOUNDED,
        "note": "Single capture, 1..127 seconds per request.",
    },
    "device.init_owner_key": {
        "status": BOUNDED,
        "note": (
            "Format the device and install an owner public key. Erases what the "
            "device holds; needs signing material from the user's own copy of the "
            "official app; slot-2 units cannot complete it at all."
        ),
    },
    "files.sync_range": {
        "status": SUPPORTED,
        "note": "Request a device-clock time range and receive CMPT containers.",
    },
    "files.inventory_totals": {
        "status": SUPPORTED,
        "note": "Aggregate record + voice-input counts and byte totals for a range.",
    },
    "crypto.owner_key_decrypt": {
        "status": SUPPORTED,
        "note": "RSA unwrap + AES-256-GCM using the caller-supplied owner private key.",
    },
    "audio.opustowav": {
        "status": BOUNDED,
        "note": ("40-byte / 20 ms Opus packets, 16 kHz mono. Native trim metadata "
                 "is not present in the container, so decoded duration is the "
                 "packet-count duration, not the original microphone duration."),
    },
    "mark.add": {
        "status": UNAVAILABLE,
        "note": ("Not reachable. The firmware has a highlight command but no BLE "
                 "producer, no app command and no physical-key trigger."),
    },
    "note.archive": {
        "status": UNAVAILABLE,
        "note": ("Manual note archive (kind2 .note.opus) is not reachable over "
                 "BLE and is not produced by the physical key on this firmware."),
    },
    "key.ai_manual": {
        "status": UNAVAILABLE,
        "note": ("The AI key acts as a recording toggle: one short press starts, a "
                 "second short press stops. Releasing never ends a recording."),
    },
}


def describe() -> dict:
    """Return a JSON-serialisable capability summary."""
    buckets = {SUPPORTED: [], BOUNDED: [], UNAVAILABLE: []}
    for name, entry in CAPABILITIES.items():
        buckets[entry["status"]].append({"name": name, "note": entry["note"]})
    return {
        "device": "心力球 / OPPO AI Pin (CNMABWC301)",
        "statuses": buckets,
        "guidance": {
            "unavailable": ("Do not build product flows on unavailable capabilities. "
                            "They are documented negatives, not missing features of this SDK."),
        },
    }
