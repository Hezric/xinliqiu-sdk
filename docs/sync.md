# Synchronisation

Three different things are called "sync". They are separate operations and the
SDK treats them separately.

| Operation | Direction | Needs a private key | API |
|---|---|---|---|
| Fetch a device range | device → host | no | `Xinliqiu.sync_range` |
| Upload ciphertext | host → store | no | `push_range` |
| Read what the store holds | store → host | no | `pull_changes` |

Decryption is never part of any of them.

---

## 1. Device to host

```python
result = ball.sync_range(start_ms, end_ms)
print(result["count"], result["bytes"])
for path in result["files"]:
    print(path)
```

The result looks like this:

    {
      "files": ["<run-dir>/<device-name>", ...],
      "count": 1,
      "bytes": 12345,
      "offers": 1,
      "rejected": [],
      "_output_dir": "<run-dir>",
    }

`files` lists every container written (device-supplied names may have any
suffix), `count` is how many, `bytes` is the payload received, `offers` counts
the file offers seen, and `rejected` holds `(transId, reason)` pairs the
receiver refused. A range that wrote no files, or that rejected an offer, is
not a success. Files land in a run directory as CMPT containers.

### The device delivers a queue, not a re-readable archive

This is the one behaviour that surprises people, so it is worth stating plainly:
a range request answers with the containers the device has **not handed over
yet**. The same container is offered once. Asking again for the same window
returns `count: 0` even though `inventory_totals()` still counts those items.

Observed on hardware: the first sync after pairing delivered everything pending;
every later sync of the same window answered the request and then stayed silent.

What follows from it:

- **Point `output_dir` at durable storage.** There is no second chance for a
  container you lose.
- **`count: 0` is an answer, not a failure.** Do not loop.
- A silent link and an empty queue look similar; the difference is whether the
  device acknowledged the range request at all. The SDK raises when it did not.

### What the receiver refuses

| Condition | Result |
|---|---|
| Offer arrives on the control channel | rejected, `reason=1` |
| Declared size above 64 MiB | rejected, `reason=9` (policy) |
| More than 4 transfers in flight | rejected, `reason=9` |
| `md5` present but not 32 hex characters | rejected, `reason=2` |
| Chunk would exceed the declared size | the transfer is abandoned |
| MD5 mismatch at the end | file removed, `reason=10` reported |

Nothing partial is ever published: bytes go to `<name>.part`, are fsync'd,
verified, and only then renamed into place. A failure leaves no file behind.

---

## 2. Host to store

```python
from aipin_sdk import CloudflareStorage, push_range

store = CloudflareStorage(url, token)
report = push_range(ball, store, device_id, start_ms, end_ms)
print(report["upload"])   # {'stored': n, 'duplicates': m, 'total': k}
```

Every CMPT entry is uploaded as its original encrypted bytes. Uploads are
idempotent: the key is derived from a content hash, so repeating a range costs
nothing beyond the round trip. The `report["device_sync"]` member carries the
same `files`, `count`, `bytes`, `offers`, `rejected` shape as `sync_range`.

---

## 3. Store to host (incremental)

A cursor-based pull, designed so two clients converge without re-downloading
everything.

```python
from aipin_sdk import pull_changes

page = pull_changes(store, device_id, since=cursor, download=True, local_dir=Path("./mirror"))
cursor = page["cursor"]          # persist this locally
while page["has_more"]:
    page = pull_changes(store, device_id, since=page["cursor"], download=True, local_dir=Path("./mirror"))
    cursor = page["cursor"]
```

### Why the cursor is not a device timestamp

Device clocks can repeat, overlap or regress. A cursor built on device time would
skip or duplicate objects. The store therefore assigns a monotonic `seq` at
insertion time and cursors reference that sequence.

The **device** time range is still available as a filter, for questions like
"what was recorded yesterday":

```python
objects = store.list(device_id, start_ms=yesterday, end_ms=today)
```

---

## 4. Cheap comparison

To decide whether two clients differ at all, compare digests instead of lists.

```python
mine = store.digest(device_id)
remote = other_client_digest      # computed by the peer and sent over

if mine["digest"] != remote["digest"]:
    pull_changes(store, device_id, since=cursor)
```

`GET /digest?device=...` returns `count`, `total_bytes`, `latest_seq` and a
`digest` computed over ordered `object_key:sha256` pairs. Equal digests mean the
sets are identical.

---

## 5. Endpoint reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/objects` | Upload one ciphertext object |
| `GET` | `/objects?device=&start_ms=&end_ms=` | Device-time filtered metadata |
| `GET` | `/objects/changes?device=&since=&limit=` | Incremental metadata by cursor |
| `GET` | `/objects/<key>` | Ciphertext bytes |
| `GET` | `/digest?device=&start_ms=&end_ms=` | Set comparison digest |
| `GET` | `/inventory?device=` | Aggregate totals |

---

## 6. Multi-client topology

```
         phone                laptop                 server
           │                    │                      │
           │   push_range       │   pull_changes       │
           └──────────► store ◄─┴──────────────────────┘
                          │
                 R2 (ciphertext) + D1 (metadata)
```

Because everything in the store is ciphertext, any number of clients can mirror
the same set. Each client keeps its own cursor and its own private key; the
store never learns either.

---

## 7. Failure handling

| Situation | Behaviour |
|---|---|
| Upload retried | duplicate, no extra storage |
| Client crashes mid-pull | cursor not advanced; next run resumes cleanly |
| Object missing in R2 | `410 Gone`, metadata row retained for diagnosis |
| Token rotated | all clients must be updated; objects untouched |
