import type { Container, ContainerEntry, EntryKind } from "./types.js";

const ENTRY_RECORD = 68;

function classify(name: string): EntryKind {
  const n = name.toLowerCase();
  if (n.endsWith(".end.enc.opus")) return "end-marker";
  if (n.endsWith(".enc.note.opus") || n.includes(".enc.note.")) return "note-audio";
  if (n.endsWith(".enc.opus")) return "audio";
  if (n.endsWith(".tp")) return "trace-point";
  return "unknown";
}

/** Parse a CMPT container. Throws on malformed input. */
export function parseCmpt(data: Uint8Array): Container {
  const buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  if (buf.length < 12 || buf.toString("latin1", 0, 4) !== "CMPT") {
    throw new Error("not a CMPT container");
  }
  const count = buf.readUInt16LE(4);
  const dataOffset = buf.readUInt16LE(6);
  const declaredLength = buf.readUInt32LE(8);
  if (12 + count * ENTRY_RECORD > buf.length) {
    throw new Error("entry table exceeds buffer");
  }
  const entries: ContainerEntry[] = [];
  let offset = dataOffset;
  for (let i = 0; i < count; i += 1) {
    const rec = 12 + i * ENTRY_RECORD;
    let end = buf.indexOf(0, rec);
    if (end < 0 || end > rec + 64) end = rec + 64;
    const name = buf.toString("utf8", rec, end);
    const size = buf.readUInt32LE(rec + 64);
    if (offset + size > buf.length) {
      throw new Error(`entry ${i} truncated`);
    }
    const payload = new Uint8Array(buf.subarray(offset, offset + size));
    entries.push({ index: i, name, size, kind: classify(name), payload });
    offset += size;
  }
  return { declaredLength, entries };
}

export function entriesByKind(container: Container, kind: EntryKind): ContainerEntry[] {
  return container.entries.filter((e) => e.kind === kind);
}
