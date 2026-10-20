# Fetch every source at send time, fall back to the last snapshot

nba-today fetches every source (schedule, yesterday's results, standings, injuries, the NBA.com enrichment feeds) in one pass at send time. Each successful raw response is written to the mounted volume as a snapshot. If a source fails at send time, the digest is built from that source's snapshot and says how stale it is. If there is no snapshot either, that section is omitted and the digest notes it. This is the decision mlb-today v2 reached (its ADR-0002). A separate pre-fetch phase was rejected because the inputs change only overnight, so it buys nothing except a second code path to keep in sync.

## Consequences

- One pipeline, one code path, one place to test.
- The volume holds required state: snapshots and the last-sent marker. Losing it degrades one day's digest at most.
- The snapshot fallback covers the NBA.com enrichment sources too (ADR-0004). A blocked day therefore uses yesterday's local TV, team ratings, and PIE table rather than dropping them.
