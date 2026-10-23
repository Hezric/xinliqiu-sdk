/** Incremental read access to a xinliqiu ciphertext store. */

export interface StoredObject {
  objectKey: string;
  entryName: string;
  kind: string;
  sizeBytes: number;
  sha256: string;
  startMs: number | null;
  endMs: number | null;
}

export interface ChangesPage {
  objects: StoredObject[];
  cursor: number;
  hasMore: boolean;
}

export interface DigestResult {
  device: string;
  startMs: number;
  endMs: number;
  count: number;
  totalBytes: number;
  digest: string;
  latestSeq: number;
}

interface RawObject {
  object_key: string;
  entry_name: string;
  kind: string;
  size_bytes: number;
  sha256: string;
  device_start_ms: number | null;
  device_end_ms: number | null;
}

function toStored(raw: RawObject): StoredObject {
  return {
    objectKey: raw.object_key,
    entryName: raw.entry_name,
    kind: raw.kind,
    sizeBytes: raw.size_bytes,
    sha256: raw.sha256,
    startMs: raw.device_start_ms,
    endMs: raw.device_end_ms,
  };
}

export class CloudflareStore {
  private readonly base: string;

  constructor(baseUrl: string, private readonly token: string) {
    this.base = baseUrl.replace(/\/$/, "");
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${this.token}`);
    const response = await fetch(this.base + path, { ...init, headers });
    return response;
  }

  /** Upload one ciphertext object. Idempotent: identical bytes dedupe. */
  async put(deviceId: string, entryName: string, kind: string, payload: Uint8Array): Promise<{ status: string; seq: number | null }> {
    const response = await this.request("/objects", {
      method: "POST",
      body: payload,
      headers: {
        "content-type": "application/octet-stream",
        "x-xinliqiu-device": deviceId,
        "x-xinliqiu-name": entryName,
        "x-xinliqiu-kind": kind,
      },
    });
    if (!response.ok) throw new Error(`upload failed: ${response.status}`);
    const body = (await response.json()) as { status: string; seq: number | null };
    return body;
  }

  /** Incremental metadata pull ordered by storage cursor. */
  async changes(deviceId: string, since = 0, limit = 500): Promise<ChangesPage> {
    const query = new URLSearchParams({ device: deviceId, since: String(since), limit: String(limit) });
    const response = await this.request(`/objects/changes?${query.toString()}`);
    if (!response.ok) throw new Error(`changes failed: ${response.status}`);
    const body = (await response.json()) as { objects: RawObject[]; cursor: number; has_more: boolean };
    return {
      objects: (body.objects ?? []).map(toStored),
      cursor: body.cursor ?? since,
      hasMore: Boolean(body.has_more),
    };
  }

  /** Device-time filtered metadata. */
  async list(deviceId: string, startMs: number, endMs: number): Promise<StoredObject[]> {
    const query = new URLSearchParams({ device: deviceId, start_ms: String(startMs), end_ms: String(endMs) });
    const response = await this.request(`/objects?${query.toString()}`);
    if (!response.ok) throw new Error(`list failed: ${response.status}`);
    const body = (await response.json()) as { objects: RawObject[] };
    return (body.objects ?? []).map(toStored);
  }

  /** Ciphertext bytes for one object. */
  async get(objectKey: string): Promise<Uint8Array> {
    const response = await this.request(`/objects/${encodeURIComponent(objectKey)}`);
    if (!response.ok) throw new Error(`get failed: ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  }

  /** Set digest used to compare two clients without transferring objects. */
  async digest(deviceId: string, startMs = 0, endMs = Number.MAX_SAFE_INTEGER): Promise<DigestResult> {
    const query = new URLSearchParams({ device: deviceId, start_ms: String(startMs), end_ms: String(endMs) });
    const response = await this.request(`/digest?${query.toString()}`);
    if (!response.ok) throw new Error(`digest failed: ${response.status}`);
    const b = (await response.json()) as Record<string, unknown>;
    return {
      device: String(b.device),
      startMs: Number(b.start_ms ?? startMs),
      endMs: Number(b.end_ms ?? endMs),
      count: Number(b.count ?? 0),
      totalBytes: Number(b.total_bytes ?? 0),
      digest: String(b.digest ?? ""),
      latestSeq: Number(b.latest_seq ?? 0),
    };
  }
}
