import type { CapabilitySummary, CapabilityTable } from "./types.js";

export const CAPABILITIES: CapabilityTable = {
  "auth.reuse_existing_ksc": {
    status: "supported",
    note: "Authenticate with an existing KSC/peer-id pair. No pairing or INIT.",
  },
  "record.status": { status: "supported", note: "Read-only state query." },
  "record.start_stop": {
    status: "bounded",
    note: "Toggle recording over BLE. No audio file without voice activity.",
  },
  "record.vad_gated_audio": {
    status: "bounded",
    note: "Audio files are created only when VAD detects speech.",
  },
  "record.segmentation": {
    status: "bounded",
    note: "Speech separated by silence produces separate files; ordinary rollover at 120,000 bytes.",
  },
  "record.silence_retention": {
    status: "bounded",
    note: "Trailing silence is retained for a bounded period only.",
  },
  "voiceprint.collect": { status: "bounded", note: "Single capture, 1..127 seconds." },
  "files.sync_range": {
    status: "supported",
    note: "Request a device-clock range and receive CMPT containers.",
  },
  "files.inventory_totals": {
    status: "supported",
    note: "Aggregate counts and byte totals; no file list.",
  },
  "crypto.owner_key_decrypt": {
    status: "supported",
    note: "RSA unwrap + AES-256-GCM with a caller-supplied key.",
  },
  "audio.opustowav": {
    status: "bounded",
    note: "40-byte / 20 ms packets; duration is packet-count based, not native trim.",
  },
  "mark.add": {
    status: "unavailable",
    note: "Not reachable: no BLE producer, no app command, no physical-key trigger.",
  },
  "note.archive": {
    status: "unavailable",
    note: "Manual note archive is not reachable over BLE.",
  },
  "key.ai_manual": {
    status: "unavailable",
    note: "The AI key is a recording toggle; releasing never ends a recording.",
  },
};

export function describe(): CapabilitySummary {
  const statuses: CapabilitySummary["statuses"] = {
    supported: [],
    bounded: [],
    unavailable: [],
  };
  for (const [name, entry] of Object.entries(CAPABILITIES)) {
    statuses[entry.status].push({ name, note: entry.note });
  }
  return {
    device: "心力球 / OPPO AI Pin (CNMABWC301)",
    statuses,
    guidance: {
      unavailable:
        "Do not build product flows on unavailable capabilities. They are documented negatives, not missing SDK features.",
    },
  };
}
