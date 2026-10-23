/**
 * Xinliqiu encrypted-object store.
 *
 * Design commitments:
 *   - Only ciphertext is ever stored. The Worker never receives, holds or
 *     returns a decryption key.
 *   - Device credentials (KSC, peer id, owner private key) must never be sent
 *     here. There is no endpoint for them.
 *   - The only secret the Worker knows is a bearer token you set yourself.
 */

const MAX_OBJECT_BYTES = 2 * 1024 * 1024;
const SHA256_RE = /^[0-9a-f]{64}$/;
const KIND_ALLOWED = new Set([
  "audio", "end-marker", "note-audio", "trace-point", "unknown",
]);

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

function unauthorized() {
  return json({ error: "unauthorized" }, 401, { "www-authenticate": "Bearer" });
}

async function authorize(request, env) {
  const header = request.headers.get("authorization") || "";
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!match) return false;
  const presented = match[1].trim();
  const expected = env.XINLIQIU_TOKEN || "";
  if (!expected || presented.length !== expected.length) return false;
  // Constant-time-ish compare.
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= presented.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function safeSegment(value) {
  return String(value).replace(/[^A-Za-z0-9._-]/g, "_");
}

async function handlePut(request, env) {
  const url = new URL(request.url);
  const deviceId = request.headers.get("x-xinliqiu-device") || "";
  const entryName = request.headers.get("x-xinliqiu-name") || "";
  const kind = request.headers.get("x-xinliqiu-kind") || "unknown";
  const declaredSha = (request.headers.get("x-xinliqiu-sha256") || "").toLowerCase();
  const startMs = Number(request.headers.get("x-xinliqiu-start-ms") || 0) || null;
  const endMs = Number(request.headers.get("x-xinliqiu-end-ms") || 0) || null;

  if (!deviceId) return json({ error: "missing x-xinliqiu-device" }, 400);
  if (!entryName) return json({ error: "missing x-xinliqiu-name" }, 400);
  if (!KIND_ALLOWED.has(kind)) return json({ error: `unsupported kind: ${kind}` }, 400);
  if (declaredSha && !SHA256_RE.test(declaredSha)) {
    return json({ error: "x-xinliqiu-sha256 must be 64 lowercase hex chars" }, 400);
  }

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_OBJECT_BYTES) return json({ error: "object too large" }, 413);

  const bytes = await request.arrayBuffer();
  if (bytes.byteLength === 0) return json({ error: "empty body" }, 400);
  if (bytes.byteLength > MAX_OBJECT_BYTES) return json({ error: "object too large" }, 413);

  const actualSha = await sha256Hex(bytes);
  if (declaredSha && declaredSha !== actualSha) {
    return json({ error: "sha256 mismatch" }, 400);
  }

  const objectKey = `${safeSegment(deviceId)}/${actualSha}`;
  const r2Key = `objects/${safeSegment(deviceId)}/${actualSha}`;

  const existing = await env.DB.prepare(
    "SELECT object_key FROM objects WHERE object_key = ?1"
  ).bind(objectKey).first();
  if (existing) {
    const prior = await env.DB.prepare("SELECT seq FROM objects WHERE object_key = ?1").bind(objectKey).first();
    return json({ status: "duplicate", object_key: objectKey, sha256: actualSha, seq: prior ? prior.seq : null }, 200);
  }

  await env.BUCKET.put(r2Key, bytes, {
    customMetadata: { device: deviceId, name: entryName, kind, sha256: actualSha },
  });

  // Assign the sequence inside the INSERT so that D1's serialized writes
  // cannot hand the same seq to two concurrent uploads.

  await env.DB.prepare(
    `INSERT INTO objects
       (object_key, device_id, entry_name, kind, size_bytes, sha256,
        device_start_ms, device_end_ms, synced_at, r2_key, seq)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
             (SELECT COALESCE(MAX(seq), 0) + 1 FROM objects))`
  )
    .bind(
      objectKey, deviceId, entryName, kind, bytes.byteLength, actualSha,
      startMs, endMs, Date.now(), r2Key
    )
    .run();

  const stored = await env.DB.prepare(
    "SELECT seq FROM objects WHERE object_key = ?1"
  ).bind(objectKey).first();
  const seq = stored ? Number(stored.seq) : null;

  return json({ status: "stored", object_key: objectKey, sha256: actualSha, size: bytes.byteLength, seq }, 201);
}

async function handleList(request, env) {
  const url = new URL(request.url);
  const deviceId = url.searchParams.get("device") || "";
  const startMs = Number(url.searchParams.get("start_ms") || 0);
  const endMs = Number(url.searchParams.get("end_ms") || Number.MAX_SAFE_INTEGER);
  const kind = url.searchParams.get("kind");
  const limit = Math.min(Number(url.searchParams.get("limit") || 200), 1000);
  if (!deviceId) return json({ error: "device query parameter required" }, 400);

  const clauses = ["device_id = ?1", "COALESCE(device_end_ms, device_start_ms, synced_at) BETWEEN ?2 AND ?3"];
  const binds = [deviceId, startMs, endMs];
  if (kind) {
    clauses.push(`kind = ?${binds.length + 1}`);
    binds.push(kind);
  }
  const sql =
    `SELECT object_key, entry_name, kind, size_bytes, sha256, device_start_ms, device_end_ms, synced_at, seq
       FROM objects WHERE ${clauses.join(" AND ")}
      ORDER BY COALESCE(device_end_ms, device_start_ms, synced_at) ASC
      LIMIT ${limit}`;
  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  return json({ objects: results || [], count: (results || []).length });
}

async function handleGet(request, env, key) {
  const row = await env.DB.prepare(
    "SELECT r2_key, size_bytes, sha256, entry_name, kind FROM objects WHERE object_key = ?1"
  ).bind(key).first();
  if (!row) return json({ error: "not found" }, 404);
  const object = await env.BUCKET.get(row.r2_key);
  if (!object) return json({ error: "object missing in R2" }, 410);
  return new Response(object.body, {
    headers: {
      "content-type": "application/octet-stream",
      "x-xinliqiu-sha256": row.sha256,
      "x-xinliqiu-name": row.entry_name,
      "x-xinliqiu-kind": row.kind,
    },
  });
}

async function handleInventory(request, env) {
  const url = new URL(request.url);
  const deviceId = url.searchParams.get("device");
  if (deviceId) {
    const row = await env.DB.prepare(
      "SELECT * FROM inventory WHERE device_id = ?1"
    ).bind(deviceId).first();
    return json({ inventory: row || null });
  }
  const { results } = await env.DB.prepare("SELECT * FROM inventory").all();
  return json({ inventory: results || [] });
}

async function handleChanges(request, env) {
  const url = new URL(request.url);
  const deviceId = url.searchParams.get("device");
  const since = Number(url.searchParams.get("since") || 0);
  const limit = Math.min(Number(url.searchParams.get("limit") || 500), 1000);
  if (!deviceId) return json({ error: "device query parameter required" }, 400);

  const { results } = await env.DB.prepare(
    `SELECT object_key, entry_name, kind, size_bytes, sha256,
            device_start_ms, device_end_ms, synced_at, seq
       FROM objects WHERE device_id = ?1 AND seq > ?2
      ORDER BY seq ASC LIMIT ${limit + 1}`
  ).bind(deviceId, since).all();

  const rows = results || [];
  const hasMore = rows.length > limit;
  const page = hasMore ? rows.slice(0, limit) : rows;
  const cursor = page.length ? page[page.length - 1].seq : since;
  return json({ objects: page, cursor, has_more: hasMore });
}

async function handleDigest(request, env) {
  const url = new URL(request.url);
  const deviceId = url.searchParams.get("device");
  const startMs = Number(url.searchParams.get("start_ms") || 0);
  const endMs = Number(url.searchParams.get("end_ms") || Number.MAX_SAFE_INTEGER);
  if (!deviceId) return json({ error: "device query parameter required" }, 400);

  const { results } = await env.DB.prepare(
    `SELECT object_key, sha256, size_bytes FROM objects
      WHERE device_id = ?1
        AND COALESCE(device_end_ms, device_start_ms, synced_at) BETWEEN ?2 AND ?3
      ORDER BY object_key ASC`
  ).bind(deviceId, startMs, endMs).all();

  const rows = results || [];
  // Deterministic digest over ordered (key, sha256) pairs.
  const material = rows.map((r) => `${r.object_key}:${r.sha256}`).join("\n");
  const digestBuffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(material));
  const digest = Array.from(new Uint8Array(digestBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const cursorRow = await env.DB.prepare(
    "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM objects WHERE device_id = ?1"
  ).bind(deviceId).first();

  return json({
    device: deviceId,
    start_ms: startMs,
    end_ms: endMs,
    count: rows.length,
    total_bytes: rows.reduce((acc, r) => acc + Number(r.size_bytes || 0), 0),
    digest,
    latest_seq: Number(cursorRow && cursorRow.latest_seq) || 0,
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") return json({ ok: true });

    if (!(await authorize(request, env))) return unauthorized();

    try {
      if (url.pathname === "/objects/changes" && request.method === "GET") {
        return handleChanges(request, env);
      }
      if (url.pathname === "/digest" && request.method === "GET") {
        return handleDigest(request, env);
      }
      if (url.pathname === "/objects" && request.method === "POST") {
        return handlePut(request, env);
      }
      if (url.pathname === "/objects" && request.method === "GET") {
        return handleList(request, env);
      }
      if (url.pathname.startsWith("/objects/") && request.method === "GET") {
        return handleGet(request, env, decodeURIComponent(url.pathname.slice("/objects/".length)));
      }
      if (url.pathname === "/inventory" && request.method === "GET") {
        return handleInventory(request, env);
      }
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "internal", detail: String(err && err.message || err) }, 500);
    }
  },
};
