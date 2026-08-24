---
title: The Cache That Lied
description: A stale-read incident, and what the TTL was actually measuring.
crosspost:
  devto: full
  linkedin: summary
  series: Incident Notes
  tags: [caching, postgres, debugging]
  hashtags: [caching, postgres]
  summary: |
    A dashboard showed order counts that were twelve minutes stale, but only
    for some customers, and only in the afternoon.

    The TTL was measured from cache *write*, not from the underlying query's
    snapshot. Under replica lag the two drift apart, so a "60 second" entry
    could be serving data several minutes older than that.

    We now stamp entries with the replica LSN rather than wall-clock time.
    That costs an extra round trip on every miss, which we decided was worth
    paying for reads that feed a number someone acts on.
---

## What we saw

Order counts on the ops dashboard were stale, but [only for some tenants](./tenancy.md).

```mermaid
graph TD;
  A[Request] --> B[Cache];
  B --> C[Replica];
```

## The measurement bug

The TTL started at cache write. See [the ADR](../decisions/0007-cache-keys.md) for
the original design, and ![the timeline](./img/timeline.png).

```python
entry.expires_at = now() + timedelta(seconds=60)  # wrong clock
```

## The trade-off

Stamping the replica LSN costs a round trip per miss.
