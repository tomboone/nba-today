# One long-running process owns the schedule

The send time moves every day (30 minutes before the earliest tip-off, Eastern), so a fixed cron entry cannot express it. nba-today is a single long-running container. Once a day at a fixed early-morning time, and on every boot, it fetches the schedule, computes the send time, and sleeps until then. This is the decision mlb-today v2 reached for the same problem (its ADR-0001), carried over unchanged.

Two alternatives were rejected:
- Host cron firing a one-shot container every few minutes. It spreads the scheduling logic across the host and the app, and still leaves "did I already send today?" for the app to answer.
- An at(1)-style two-stage job. It reproduces the self-scheduling pattern the design avoids.

## Consequences

- The container must be restart-safe. On boot it recomputes today's send time and checks the last-sent marker, so a restart never sends twice or skips a day.
- The morning plan must run before the earliest possible send time. Regular-season games abroad (Europe, Mexico City) tip around 14:00–15:00 Eastern, so a 03:00 plan leaves ample margin.
- There is no HTTP server. Container health is a CLI check against the state file, not an endpoint.
