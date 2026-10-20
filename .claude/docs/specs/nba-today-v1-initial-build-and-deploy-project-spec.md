# nba-today v1: initial build and deploy — project spec

Linear initiative: nba-today (I-12). Project: P-NEU-100. Glossary: `CONTEXT.md` at the repo root. Decisions and their rationale: `docs/adr/`.
This spec is the shared reference for every ticket in the project. A ticket needs its own `/planit` spec only where this document is silent.

Reference implementation: mlb-today on the `release/v2` branch (`~/projects/mlb-today`, tomboone/mlb-today). Where this spec says **"port from mlb-today"**, copy the module, rename `mlb_today` to `nba_today`, and change only what this spec calls for. Do not extract a shared package.

## 1. Purpose

Build nba-today, from an empty repo, into a single containerized Python service. It runs unattended on the home Mac mini `kumadrome` (Ubuntu) and sends one NBA **digest** email per **game day**, 30 minutes before the day's earliest **tip-off**. On a no-game day that follows a game day, it sends a **scores-only digest** at a fixed morning time.

## 2. Decisions (summary; see ADRs for rationale)

| Area | Decision |
|---|---|
| Runtime | One long-running container with an internal scheduler (ADR-0001). No HTTP server. |
| Pipeline | Fetch every source at send time in one pass. Persist each successful raw response as a **source snapshot** and fall back to it on failure (ADR-0002). |
| Sources | ESPN's unofficial API is primary, over plain httpx. NBA.com (CDN schedule and stats.nba.com) is **enrichment**, reached through `curl_cffi` with Chrome impersonation; its failure never blocks a send (ADR-0004). |
| Email authoring | MJML compiled by `mjml-python`, rendered through Jinja2 (ADR-0003). |
| Transport | `Transport` protocol: Resend (primary), SMTP/Fastmail (fallback), and a dry-run outbox. Port from mlb-today. |
| Counted games | Regular season (including NBA Cup group games), NBA Cup knockouts, play-in, playoffs. Never preseason, the All-Star game, or Summer League. |
| No-game days | A scores-only digest at `SCORES_ONLY_TIME` when yesterday was a game day; otherwise nothing. |
| Matchup content | Tip-off, records, last-10 and streak, arena, national and local TV and streaming, injury report (names grouped by status), team ratings, key player, and a Cup-knockout or postseason label. |
| Result content | Final score with overtime, each team's **game leaders** (points, rebounds, assists), or a postponed status. |
| Standings | Two conference tables in seed order. Lines after seed 6 and seed 10, plus clinch and elimination marks. |
| Leaderboard | The top 25 **qualified players** by PIE: Player, Team, PTS, REB, AST, TS%, PIE. |
| Postseason | Series state on postseason matchups and results, and a **postseason picture** in place of standings. Built in the final milestone (§13). |
| Toolchain | uv, ruff (lint and format), pyright (strict), pytest, respx, syrupy, httpx, curl_cffi, pydantic v2 plus pydantic-settings, typer, Python 3.13. Same `pyproject.toml` tool config as mlb-today. |
| Build and deploy | GitHub Actions lints and tests every PR. On push to `main` it builds and pushes `ghcr.io/tomboone/nba-today` (`:latest` and `:sha-<7>`). The repo and package are public, MIT. On kumadrome, a Watchtower **scoped to nba-today** pulls new images. |
| Alerting | Structured JSON logs to stdout. healthchecks.io ping on success, `/fail` on a failed send. No Sentry. |
| Branch flow | Greenfield: every ticket branches from and PRs into `main`. There is no release branch. |

## 3. Architecture

### 3.1 Package layout (`src` layout, package `nba_today`)

```
src/nba_today/
  __init__.py
  __main__.py            # `python -m nba_today` → cli
  cli.py                 # typer: run | send | preview | health
  settings.py            # pydantic-settings; all env in one place                       (port)
  logging.py             # JSON log formatter                                             (port)
  scheduler.py           # daily loop: plan, sleep, dispatch                              (port, adapt)
  state.py               # last-sent marker on the data volume                            (port)
  clock.py               # Clock protocol, SystemClock                                    (port)
  season.py              # season helpers: label "2026-27", ESPN year 2027, phase
  teams.py               # canonical team table: 30 rows, ESPN id/abbr ↔ NBA id/tricode
  heartbeat.py           # healthchecks.io client                                         (port)
  sources/
    http.py              # make_client: shared httpx.Client factory                       (port)
    nba_http.py          # make_nba_session: curl_cffi Session (impersonate=chrome, nba.com headers)
    snapshot.py          # SnapshotStore                                                  (port)
    fetched.py           # Fetched[T] + fetch_or_snapshot                                 (port)
    espn.py              # ESPN client: scoreboard, standings, injuries, athlete stats
    nba.py               # NBA.com client: season schedule, team advanced, player base + advanced
    models/              # pydantic models of raw payloads, one module per endpoint; the scoreboard
                         # module also holds `is_counted`, so `digest/` can use it without httpx
  digest/
    models.py            # Digest and children (§6)
    bundle.py            # SourceBundle: one Fetched[...] | None per snapshot name
    build.py             # raw payloads → Digest (pure functions, no I/O)
  render/
    templates/digest.mjml.j2
    render.py            # Digest → Rendered(subject, html, text)
  transport/
    base.py  resend.py  smtp.py  chain.py  outbox.py                                      (port)
```

Rules:
- `sources/` and `transport/` are the only modules that do network I/O.
- `digest/build.py` and `render/` are pure and fully unit-tested from fixtures.
- `scheduler.py` orchestrates and is tested with a fake `Clock`.
- `nba_http.py` is the only importer of `curl_cffi`.

### 3.2 Daily flow

1. **On boot, and every day at `PLAN_TIME` (default 03:00 America/New_York),** fetch today's ESPN scoreboard (Eastern date) and keep only counted games (§5.1).
   - **Counted games today:** send time = earliest tip-off − `SEND_LEAD_MINUTES` (default 30). If that instant has already passed, send immediately unless the last-sent marker says today's digest already went out.
   - **No counted games today, but some yesterday:** send time = `SCORES_ONLY_TIME` (default 09:00 America/New_York).
   - **Neither:** log, and sleep until the next `PLAN_TIME`.
2. **At send time,** run `send(date)`:
   1. Fetch all sources (§5). Each success writes a snapshot. Each failure logs a warning and loads that source's snapshot, recording `stale[source] = snapshot_time` on the digest. A source with no snapshot either goes into `unavailable`, and its dependent sections or fields are omitted.
   2. Build the `Digest` (pure).
   3. Render MJML → HTML plus a plain-text alternative.
   4. Send through the transport chain. The first success wins.
   5. Write the last-sent marker and ping the heartbeat. If every transport fails: ping `/fail`, log at error, and do **not** write the marker, so a restart retries.
3. Sleep until the next `PLAN_TIME`.

Date semantics: "today" and "yesterday" are Eastern calendar dates everywhere. Rescheduled tip-offs are not followed: the send time is fixed at plan time.

### 3.3 Restart safety

The last-sent marker on the data volume is the only thing that prevents a double send. On boot the scheduler reads it before deciding whether an already-past send time should fire. The container restarts on failure (`restart: unless-stopped`).

### 3.4 Health

`nba-today health` exits 0 if the state file was touched within the last 26 hours; the scheduler touches it at each plan and each send. Docker's `HEALTHCHECK` runs that command. There is no HTTP endpoint.

## 4. Teams and seasons

### 4.1 Teams (`teams.py`)

The two sources disagree on team identity. ESPN uses its own team ids and abbreviations such as `NY`, `GS`, `SA`, `NO`, `UTAH`, `WSH`. NBA.com uses team ids like `1610612738` and tricodes like `NYK`, `GSW`, `SAS`, `NOP`, `UTA`, `WAS`.

`teams.py` holds a static 30-row table:

```
Team(espn_id, espn_abbr, nba_id, tricode, conference: "East" | "West", short_name)
```

It has lookups by `espn_id`, `espn_abbr`, `nba_id`, and `tricode`. The canonical display abbreviation everywhere in the digest is the **NBA tricode**. A test asserts there are 30 unique rows and 15 teams per conference. An unknown id raises; the source layer logs and skips that row, and never crashes the digest.

Games are matched across sources on **(Eastern date, home tricode, away tricode)**.

### 4.2 Seasons (`season.py`)

- A season is named by its start year. For a date `d`, the season start year is `d.year` if `d.month >= 8`, else `d.year - 1`.
- NBA.com's season label is `"2026-27"`. ESPN's `season` parameter is the **end** year, `2027`.
- Pass the season and season type explicitly on every ESPN and NBA.com request. ESPN's defaults point at the previous season or the preseason around October.
- ESPN season types: `2` regular, `3` postseason. The type used for the play-in (believed `5`) and for the NBA Cup final is pinned against recorded 2025-26 fixtures in the scoreboard ticket. The counted-game filter is defined there and nowhere else.

## 5. Sources

### 5.0 Clients

- **ESPN** goes through the shared `httpx.Client` ported from mlb-today: 20 s timeout, 3 retries with backoff on 5xx and connect errors, a descriptive `User-Agent`.
- **NBA.com** goes through `make_nba_session()`: a `curl_cffi.requests.Session(impersonate="chrome")` with `Origin: https://www.nba.com`, `Referer: https://www.nba.com/`, and `Accept: application/json`, plus the same timeout and retry policy. Both the impersonation and the headers are required; testing on 2026-10-20 showed that either alone gets a 403 or an HTML block page. Tests mock this session at the module boundary (respx cannot intercept curl_cffi): `nba.py` takes a session-like object with a `get(url, params)` method, and tests pass a fake.
- `NBA_ENRICHMENT=false` skips every NBA.com fetch and treats them as unavailable. This is the kill switch for days when Akamai is hostile.
- Pydantic models tolerate missing fields and string-typed numbers: coerce, default to `None`, never crash the digest. Models mirror the API's field names; no renaming happens at this layer.

### 5.1 ESPN (primary)

| Snapshot name | Request | Used for |
|---|---|---|
| `espn_scoreboard_today` | `GET {ESPN_SITE_API_BASE}/apis/site/v2/sports/basketball/nba/scoreboard?dates={YYYYMMDD}` | Plan and matchups |
| `espn_scoreboard_yesterday` | same, for `d-1` | Results |
| `espn_standings` | `GET {ESPN_SITE_API_BASE}/apis/v2/sports/basketball/nba/standings?season={espn_year}&seasontype=2` | Standings, last-10, streak, team games played |
| `espn_injuries` | `GET {ESPN_SITE_API_BASE}/apis/site/v2/sports/basketball/nba/injuries` | Injury report |
| `espn_athletes` | `GET {ESPN_WEB_API_BASE}/apis/common/v3/sports/basketball/nba/statistics/byathlete?season={espn_year}&seasontype=2&isqualified=false&limit=1000&sort=offensive.avgPoints:desc` | Key players |

Scoreboard fields:
- `events[].date` (UTC tip-off), `events[].season.type` (counted-game filter), `status.type.{name,state,completed,detail}`, `status.period`.
- `competitions[0]`: `venue.fullName`, `venue.address.{city,state}`, `notes[].headline` (Cup and postseason label), `series` (postseason, §13), and `broadcasts[{market, names}]` (the national fallback).
- `competitors[]`: `homeAway`, `team.{id,abbreviation}`, `score`, `records[type=total].summary`, and `leaders[name in points|rebounds|assists].leaders[0].{value, athlete.displayName}`.
- Overtime: `status.period - 4` when `> 0`.
- Statuses: `STATUS_FINAL`, `STATUS_POSTPONED`; any other non-final status renders verbatim from `detail`.

Standings fields: `children[]` (one per conference) → `standings.entries[]` → `team.{id,abbreviation}` and `stats[name]` for `wins`, `losses`, `winPercent`, `gamesBehind`, `playoffSeed`, `streak`, `clincher`, plus `Last Ten Games` (`displayValue`).
- Entries are **not sorted**: sort by `playoffSeed`.
- `clincher` codes seen in 2025-26: `z` (clinched best record in conference), `*` (clinched best record in league), `y` (clinched division), `x` (clinched playoff berth), `xp`/`pb` (clinched play-in, seeds 7-8 and 9-10), `e` (eliminated). Map them to the enum in §6: `z` and `*` are both `best_record`, `xp` and `pb` both `play_in`, and an unknown code is `None` with a warning. Pinned against the final 2025-26 standings fixture in NEU-1786 (DET is `z`, OKC `*`).

Injuries: `injuries[]` per team → `injuries[].{status, athlete.displayName, athlete.team.abbreviation}`. `status` is `Out` or `Day-To-Day`. Any other value renders verbatim.

Athletes: per athlete, team, `general.gamesPlayed`, `general.avgMinutes`, `offensive.avgPoints`, `general.avgRebounds`, `offensive.avgAssists`. The exact category for each field is pinned against the recorded fixture. A team's games played comes from `espn_standings` (`wins + losses`).

### 5.2 NBA.com (enrichment)

| Snapshot name | Request | Used for |
|---|---|---|
| `nba_schedule` | `GET {NBA_CDN_BASE}/static/json/staticData/scheduleLeagueV2_1.json` (≈7.5 MB, whole season) | Local TV and streaming; game labels |
| `nba_team_advanced` | `GET {NBA_STATS_BASE}/stats/leaguedashteamstats?MeasureType=Advanced&PerMode=PerGame&Season={label}&SeasonType=Regular%20Season&LeagueID=00` (plus the other required blank params, as nba_api sends them) | Team ratings; team games played for qualification |
| `nba_player_advanced` | `GET {NBA_STATS_BASE}/stats/leaguedashplayerstats?MeasureType=Advanced&PerMode=PerGame&Season={label}&SeasonType=Regular%20Season&LeagueID=00` (+ blanks) | PIE, TS%, GP, MIN |
| `nba_player_base` | same with `MeasureType=Base` | PTS, REB, AST |

stats.nba.com responses are `resultSets[0].{headers, rowSet}`. Parse each row into a model by zipping it with `headers`.

Schedule fields: `leagueSchedule.gameDates[].games[]` → `gameDateTimeUTC`, `homeTeam.teamTricode`, `awayTeam.teamTricode`, `gameLabel`, `gameSubLabel`, `seriesText`, `ifNecessary`, and `broadcasters.{nationalTvBroadcasters, nationalOttBroadcasters, homeTvBroadcasters, homeOttBroadcasters, awayTvBroadcasters, awayOttBroadcasters}[].broadcasterDisplay`. Radio is ignored.

Team advanced fields: `TEAM_ID`, `GP`, `OFF_RATING`, `DEF_RATING`, `NET_RATING`, `PACE`.
Player fields: `PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `GP`, `MIN`, `PIE`, `TS_PCT` (advanced); `PTS`, `REB`, `AST` (base). Base and advanced rows are joined on `PLAYER_ID`.

## 6. Digest model (what the email shows)

```
Digest
  date: date                       # Eastern
  kind: "full" | "scores_only"
  phase: "regular" | "postseason"  # postseason swaps standings → postseason picture (§13)
  stale: dict[str, datetime]       # snapshot name → snapshot time, when a fallback was used
  unavailable: list[str]           # snapshot names with no snapshot either
  matchups: list[Matchup]          # empty on scores_only; sorted by tip-off, then home tricode
  results: list[Result]            # yesterday's counted games, in tip-off order
  standings: list[ConferenceStandings] | None   # East, West; None when unavailable
  leaderboard: Leaderboard | None  # None when unavailable or no qualified players

Matchup: tip_off (tz-aware), label: str | None, arena: str | None, city: str | None,  # None: no venue
         away: MatchupSide, home: MatchupSide, tv: Broadcasts,
         series: SeriesState | None        # playoff games only (§13)
SeriesState: round_label: str | None, game_number: int | None, summary: str | None  # "BOS leads 3-1"
MatchupSide: tricode, wins: int | None, losses: int | None,  # scoreboard record, else standings
             last_ten: str | None, streak: str | None,
             injuries: InjuryReport, ratings: TeamRatings | None, key_player: KeyPlayer | None
InjuryReport: out: list[str], day_to_day: list[str], other: list[tuple[str, str]]  # (name, status)
TeamRatings: off_rtg, def_rtg, net_rtg, pace
KeyPlayer: name, ppg, rpg, apg
Broadcasts: national: list[str], home: list[str], away: list[str]   # TV and streaming merged, deduped, in source order

Result: status: "final" | "postponed" | str, overtimes: int (0 = regulation), label: str | None,
        away: ResultSide, home: ResultSide, series: SeriesState | None
ResultSide: tricode, score: int | None, leaders: GameLeaders | None
GameLeaders: points: Leader | None, rebounds: Leader | None, assists: Leader | None
Leader: name, value: int

ConferenceStandings: conference: "East" | "West", rows: list[StandingsRow]   # 15, by seed
StandingsRow: seed, tricode, wins, losses, pct: str (".732"), gb: str ("-" for the leader),
              last_ten, streak, clinch: Clinch | None
Clinch: enum of best_record | division | playoff | play_in | eliminated

Leaderboard: rows: list[LeaderRow] (≤25, by PIE desc), as_of: datetime
LeaderRow: rank, name, tricode, pts, reb, ast, ts_pct, pie
```

Build rules:
- **Counted-game filter** applies to both scoreboards (§4.2).
- **Broadcasts:** if the NBA schedule is available (fresh or stale), use its national, home, and away lists for the matched game. Otherwise, or when no NBA game matches, use ESPN `broadcasts`: `market` national/home/away maps to the same buckets, and a `NBA League Pass` name is dropped.
- **Label:** ESPN `notes[0].headline` if present, else NBA `gameLabel` · `gameSubLabel`, else `None`. A regular-season game keeps its label only if it is an NBA Cup knockout (the label names the NBA Cup and not a group): Cup group games (`NBA Cup - Group Play`) and one-offs such as `NBA Mexico City Game 2025` carry no label.
- **Matchups:** only today's counted games, and only those whose tip-off falls on the digest date (Eastern), since a stale snapshot can be an earlier day's scoreboard. Records come from the scoreboard's `records[type=total]`, else from `espn_standings` (the Cup final has no scoreboard record).
- **Results:** only yesterday's counted games whose tip-off falls on the day before the digest date (Eastern), for the same stale-snapshot reason, in tip-off order. A game that never tipped off (postponed, or any status at period 0) has no score or leaders, since ESPN sends `"0"` placeholders. A non-final status renders ESPN's `detail`, else its status name. A side with no leader category at all has `leaders = None`. Decided in NEU-1785.
- **Qualified player:** `player.GP >= ceil(0.70 * team_games_played)` and `player.MIN >= 20.0`. For the leaderboard, team games played comes from `nba_team_advanced.GP`, keeping the leaderboard NBA-only. For key players, it comes from `espn_standings`, keeping that ESPN-only. A team with no qualified player has `key_player = None`.
- **Key player:** the qualified player on that team with the highest PPG. Ties break on RPG, then name.
- **Leaderboard:** qualified players sorted by PIE desc, then PTS desc; top 25. `ts_pct` and `pie` render as percentages with one decimal.
- **Injuries:** matched to a side by team tricode via `teams.py`. The order within a status follows ESPN's.
- **Standings:** `gb` is `"-"` for seed 1 of each conference.
- **First days of a season:** before any regular-season game is final, yesterday's results may be empty (the section is omitted with no note) and the leaderboard may have no qualified players (the section is omitted with a one-line note, "No qualified players yet").

Section order in the email:
1. Header: the date, and a stale/unavailable banner if any.
2. Today's matchups, grouped by tip-off. Omitted on scores-only.
3. Last night's results.
4. Standings (or the postseason picture).
5. PIE leaderboard.
6. Footer: sources and attribution ("Data: ESPN, NBA.com. Not affiliated with the NBA.").

Subject: `NBA Today — {Mon DD}` for full, `NBA Scores — {Mon DD}` for scores-only. The plain-text alternative is generated from the same `Digest`. The HTML budget is 150 KB, asserted by a test.

## 7. Transport

Port from mlb-today unchanged: `Transport` protocol, `Message`, `SendReceipt`, `ChainTransport`, `AllTransportsFailed`, Resend, SMTP (`SMTP_SSL`), and the dry-run outbox writing `DATA_DIR/outbox/<date>.html`. `TRANSPORTS` sets the order. A transport whose config is missing is skipped with a warning.

## 8. Configuration (env, via pydantic-settings; `.env` on kumadrome, never committed)

| Var | Default | Notes |
|---|---|---|
| `TZ_LOCAL` | `America/New_York` | date and time anchor |
| `PLAN_TIME` | `03:00` | daily plan |
| `SEND_LEAD_MINUTES` | `30` | |
| `SCORES_ONLY_TIME` | `09:00` | |
| `DATA_DIR` | `/data` | snapshots, state, outbox |
| `RECIPIENTS` | required | comma-separated |
| `RESEND_API_KEY`, `RESEND_FROM` | | enables Resend |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER_ADDRESS` | `smtp.fastmail.com`, `465` | enables SMTP |
| `TRANSPORTS` | `resend,smtp` | chain order |
| `HEALTHCHECKS_URL` | | absent = heartbeat disabled |
| `DRY_RUN` | `false` | |
| `LOG_LEVEL` | `INFO` | |
| `NBA_ENRICHMENT` | `true` | kill switch for every NBA.com fetch |
| `ESPN_SITE_API_BASE` | `https://site.api.espn.com` | |
| `ESPN_WEB_API_BASE` | `https://site.web.api.espn.com` | |
| `NBA_CDN_BASE` | `https://cdn.nba.com` | |
| `NBA_STATS_BASE` | `https://stats.nba.com` | |

## 9. CLI

- `nba-today run`: the scheduler loop (container entrypoint).
- `nba-today send [--date YYYY-MM-DD] [--dry-run]`: build and send one digest now.
- `nba-today preview [--date] [--out digest.html] [--from-snapshots]`: render to a file without sending.
- `nba-today health`: healthcheck exit code.

## 10. Testing conventions

- **Fixtures.** `tests/fixtures/{espn,nba}/*.json` hold recorded real responses captured by `scripts/record_fixtures.py`, which uses the real clients, including curl_cffi for NBA. Trim them to a few games, a few teams' injuries, and about 40 players. The 7.5 MB schedule is trimmed to the dates the tests use. Fixtures to record from 2025-26 dates: a regular-season night with an overtime game; a night with a postponement if one exists (else hand-edit a copy and name it `*_edited.json`); a Cup knockout night; the Cup final; a play-in night; a playoff night; the final regular-season standings (all clinch codes). Also record the current 2026-27 opening night and standings.
- **Client tests.** respx mocks ESPN at the httpx boundary. A fake session stands in for curl_cffi in NBA tests. Client tests assert request params and parsed models.
- **`digest/build.py` tests** are pure: fixture payloads in, `Digest` out. Cases include: overtime and double overtime; postponed; a counted-game filter that drops preseason and the All-Star game; a Cup knockout label; NBA schedule unavailable (ESPN broadcast fallback, League Pass dropped); team advanced unavailable (no ratings); a team with no qualified player; leaderboard qualification at the 70% boundary; tie-breaks; a game that won't match across sources; standings sort and every clinch code; empty yesterday; no qualified players.
- **Rendering.** syrupy snapshot tests of the compiled HTML for a full digest, a scores-only digest, a stale digest, and an enrichment-unavailable digest. Assert the HTML is under 150 KB.
- **Scheduler.** Port mlb-today's fake-`Clock` cases: boot before send, boot after send with the marker, boot after send without the marker, no games, scores-only day, early tip (a 13:00 ET game abroad).
- **Transport.** Port mlb-today's tests.
- **`teams.py`.** 30 rows, unique on every key, 15 per conference.
- **CI** fails on ruff, pyright, or pytest. Coverage is reported, not gated.

## 11. Container and deploy

- **`Dockerfile`:** port from mlb-today (multi-stage `python:3.13-slim`, `uv sync --frozen --no-dev`, non-root user, `HEALTHCHECK CMD nba-today health`, `CMD ["nba-today", "run"]`). Check that the `curl_cffi` wheel installs on the slim image for the build arch.
- **Workflows:** `ci.yml` (PRs and pushes: uv sync, ruff check, ruff format --check, pyright, pytest) and `image.yml` (push to `main` → `:latest` and `:sha-<7>`). Port from mlb-today. There is no `:edge` tag.
- **`deploy/docker-compose.yml`:** the `nba-today` service (`ghcr.io/tomboone/nba-today:latest`, `env_file: .env`, `./data:/data`, `restart: unless-stopped`, labels `com.centurylinklabs.watchtower.enable=true` and `com.centurylinklabs.watchtower.scope=nba-today`) and a `watchtower` service (`containrrr/watchtower:1.7.1`, `--label-enable --scope nba-today --interval 300 --cleanup`, `DOCKER_API_VERSION=1.44`).
- **Watchtower scoping, to verify in the deploy ticket:** kumadrome already runs mlb-today's *unscoped* `--label-enable` Watchtower. Confirm from Watchtower's docs or source whether an unscoped instance ignores containers with a scope label. If it does not, file a one-line ticket in mlb-today to add `--scope mlb-today` and the matching label.
- **`deploy/README.md`:** port from mlb-today, trimmed for kumadrome. Docker is already installed there. Cover: a sparse clone of `deploy/` into `~/nba-today`, `.env`, `docker compose up -d`, logs, and running `preview`. Remote `sudo` is not available to Claude; any `sudo` step is handed to the user as `ssh -t kumadrome 'sudo …'`.
- **External accounts:** a new healthchecks.io check with period 1 day and grace 12 hours. NBA send times swing further than MLB's: a noon holiday tip followed by a 7:30 PM tip the next day is 31.5 h between pings. Pause the check over the All-Star break and the off-season, as mlb-today's README does. Resend's verified domain is reused from mlb-today.

## 12. Go-live (operational milestone)

1. Run `:latest` on kumadrome with `DRY_RUN=true` for two game days. Inspect `DATA_DIR/outbox/` and the logs, especially whether NBA.com fetches succeed from kumadrome's IP.
2. Set `DRY_RUN=false`, confirm one real digest arrives, and confirm the healthchecks.io ping.
3. Make the GHCR package public, if the workflow didn't, so Watchtower can pull anonymously.

## 13. Postseason (final milestone)

Built after go-live and verified against 2025-26 postseason fixtures before the April 2027 play-in.

- **Phase detection:** the digest's `phase` is `postseason` when any counted game today or yesterday (the same games the matchups and results read) has ESPN season type 3 or 5. Decided in NEU-1793.
- **Series state:** on playoff matchups and results (season type 3 only; the play-in keeps its label and has no series), pinned against the 2025-26 fixtures in NEU-1793. The round and game number come from ESPN's note (`East 1st Round - Game 4`), else NBA `gameLabel` and `seriesGameNumber`. The summary is built from ESPN `series.competitors[].{id, wins}` and `completed` in NBA.com's wording and tricodes (`BOS leads 3-1`, `Series tied 2-2`, `NYK wins 4-1`), else NBA `seriesText`. ESPN's own `summary` is not used: it carries ESPN abbreviations (`SA`). ESPN also attaches a playoff `series` to the regular-season meeting of the same teams, which is why the season type gates it. The email shows the series line in place of the label.
- **Postseason picture:** replaces standings. Play-in games (with results) while the play-in runs, then each conference's series by round with their state. Source: an ESPN scoreboard date-range query over the postseason so far (`dates=YYYYMMDD-YYYYMMDD`), reduced to series, under a tenth snapshot name `espn_postseason` (fetched only in the postseason phase). Note: on 2026-10-20 that range query returned `400 Failed to get events endpoint` for every range tried (a week, a month, ten weeks), while single dates worked. If that proves unworkable, a `/planit` pass on that ticket picks the NBA schedule's `seriesText` instead.
- **Leaderboard and team ratings** stay on regular-season stats during the postseason.

## 14. Out of scope

HTTP endpoints of any kind, box scores or play-by-play, quarter linescores, per-user preferences or multiple recipients with different content, live or in-game updates, Sentry or any APM, a web archive of past digests, preseason, All-Star and Summer League games, betting lines, and a shared package with mlb-today.
