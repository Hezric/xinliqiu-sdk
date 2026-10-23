export type CapabilityStatus = "supported" | "bounded" | "unavailable";

export interface CapabilityEntry {
  status: CapabilityStatus;
  note: string;
}

export type CapabilityTable = Record<string, CapabilityEntry>;

export interface CapabilitySummary {
  device: string;
  statuses: Record<CapabilityStatus, Array<{ name: string; note: string }>>;
  guidance: { unavailable: string };
}

export interface Credentials {
  deviceId: string;
  peerIdFile: string;
  keyFile: string;
}

export interface RecordStatus {
  recording: boolean;
  batteryPercent: number | null;
  errorCode: number | null;
}

export interface InventoryTotals {
  count: number;
  bytes: number;
  highlightCount: number;
  highlightBytes: number;
  hasFileList: boolean;
  hasCursor: boolean;
}

export type EntryKind = "audio" | "end-marker" | "note-audio" | "trace-point" | "unknown";

export interface ContainerEntry {
  index: number;
  name: string;
  size: number;
  kind: EntryKind;
  payload: Uint8Array;
}

export interface Container {
  declaredLength: number;
  entries: ContainerEntry[];
}
