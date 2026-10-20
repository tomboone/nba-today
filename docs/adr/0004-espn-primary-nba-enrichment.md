# ESPN is the primary source; NBA.com is optional enrichment

The NBA has no stable public API like the MLB Stats API. Testing on 2026-10-20 from the home residential IP found that `cdn.nba.com` and `stats.nba.com` reject ordinary HTTP clients (httpx, requests, curl), even with browser headers. Akamai fingerprints the TLS handshake, and the endpoints answered only to `curl_cffi` imitating Chrome with nba.com `Origin`/`Referer` headers. `nba_api` is blocked as shipped, and its tracker logged Akamai outages in September and October 2026. ESPN's unofficial `site.api.espn.com` endpoints answered plain httpx and cover the schedule, results with per-team leaders, standings with seeds and clinch codes, injuries, and leaders.

So ESPN, through the shared httpx client, is the only source the digest needs in order to send. One NBA.com client, using `curl_cffi` and kept in its own module, adds what ESPN lacks: local TV and team streaming from `scheduleLeagueV2_1.json`, plus team ratings and the PIE leaderboard from `stats.nba.com`. A failed NBA.com fetch falls back to that source's snapshot (ADR-0002) like any other source. With no snapshot, matchups use ESPN's national-only broadcasts, team ratings are dropped, and the leaderboard is omitted. NBA.com never blocks a send.

Rejected alternatives:
- ESPN only: loses local TV, and team ratings would have to be estimated.
- NBA.com as primary: the whole digest would then hinge on Akamai not tightening its fingerprinting.
- balldontlie: standings and leaders need the $39.99/mo tier, and it has no broadcast data.
- Basketball-Reference: its data-use policy forbids tools built on scraped data.

## Consequences

- `curl_cffi` is a dependency. It ships prebuilt wheels for linux/amd64 and arm64, so the container stays slim.
- ESPN's terms prohibit automated access, and both sources are undocumented. Recorded fixtures and pydantic models that tolerate missing fields are the guard against schema drift.
- Every ESPN request passes `season` and `seasontype` explicitly. ESPN's defaults point at the previous season or the preseason around October.
- ESPN and NBA.com identify games differently, so the two are matched on Eastern date plus the home and away team tricodes.
