# Operational notes on device state

Three behaviours show up in practice that are not obvious from the protocol, and
each costs time if you meet it without warning.

## Rapid re-pairing is not reliable

The device holds two pairing slots and evicts by least-recently-used. Pairing
again and again in quick succession does not always leave a usable credential:
the handshake reports success, then the very next authentication fails with a
key mismatch.

This is the device, not this library. The same sequence fails the same way with
the reference implementation, on alternating attempts, while a pairing left
alone for a few seconds works first time.

**What to do:** if `connect()` fails during authentication right after pairing,
wait a few seconds and pair once more. Do not loop tightly - each attempt
rewrites a slot.

## The device delivers a queue, not a re-readable archive

A sync answers with the containers the device has **not handed over yet**. It is
a delivery queue, not a range query over stored audio: the same container is
offered once, and asking again for the same window returns `count: 0` even
though `inventory_totals()` still counts it.

Observed on hardware: the first sync after pairing delivered everything pending
(two containers, 3,884,832 bytes); every later sync of the same window answered
the request and then stayed silent.

**What this means:** you get one chance per container. Point `output_dir` at
durable storage, and treat a `count: 0` answer as "nothing new", not as a
retryable failure. Do not loop.

## The system service is refused when the host offers it

The `system:filetransfer` service is device-initiated. Sending our own offer
usually comes back with status 3, and that refusal is normal: the device manages
the profile itself and opens it when it has something to push. `connect()` logs
it as `system_service_refused` and continues; the device's own service request
declares the control and data channels later, and the sync layer answers it.

Do not treat that refusal as a broken link.

## Polling cadence

Poll infrequently. This is a small battery-powered wearable: every call opens a
link, runs a five-stage handshake, and closes again. For anything resembling
monitoring, keep the process short-lived or space calls out by tens of seconds
rather than seconds.