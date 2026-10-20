# nba-today

A single-person daily NBA digest: one email per game day, sent shortly before the first tip-off, with today's matchups, last night's results, the standings, and the season leaderboards.

## Language

### The deliverable

**Digest**:
The one email nba-today sends per day. A full digest has matchups, results, standings (or the postseason picture), and leaderboards.
_Avoid_: Report, newsletter, email data

**Scores-only digest**:
A digest sent on a day with no games that follows a game day. It has results, standings, and leaderboards but no matchups.

**Send time**:
The instant a digest is dispatched. On a game day it is 30 minutes before the earliest tip-off; on a scores-only day it is a fixed morning time.
_Avoid_: Schedule, cron

**Heartbeat**:
The signal sent to the dead-man monitor after a digest is successfully dispatched. Its absence on a game day is the alarm. When every transport fails, a `/fail` ping raises the alarm at once instead.

### The basketball

**Game day**:
An Eastern-time calendar date on which at least one counted game is scheduled. "Today" and "yesterday" in nba-today always mean Eastern-time dates.
_Avoid_: Today

**Counted game**:
A regular-season game (including NBA Cup group games), an NBA Cup knockout game, a play-in game, or a playoff game. Preseason, the All-Star game, and Summer League are not counted and never appear in a digest.

**Tip-off**:
The scheduled start time of a game.
_Avoid_: Start time, first pitch

**Matchup**:
One scheduled game on a game day: both teams with their records, last-10 and streak, tip-off, arena, broadcasts, each side's injury report, team ratings, and key player. A Cup knockout or postseason game also carries its label (e.g. "NBA Cup Semifinal", "East First Round · Game 3").
_Avoid_: Game (ambiguous with Result)

**Injury report**:
The players listed as injured for a team on the day of a matchup, grouped by status (Out, Day-To-Day).

**Team ratings**:
A team's season-to-date offensive, defensive, and net rating, and pace, shown beside it in a matchup.

**Key player**:
A team's leading scorer for the season among its qualified players, shown with his points, rebounds, and assists per game in a matchup.

**Qualified player**:
A player who has appeared in at least 70% of his team's games so far and averages at least 20 minutes per game.

**Result**:
One game from the previous game day as it stands at send time: the final score with any overtime, each team's game leaders, or a non-final status (postponed).
_Avoid_: Score, box score

**Game leaders**:
For one team in one result, the player with the most points, the player with the most rebounds, and the player with the most assists, each with that total.
_Avoid_: Top performers, stars

**Standings**:
The two conference tables (East, West) as of the previous game day, in seed order, marking the playoff line (after seed 6), the play-in line (after seed 10), and clinched or eliminated teams. Regular season only.

**Seed**:
A team's position in its conference standings, 1–15.

**Postseason**:
The play-in tournament and the playoffs. During the postseason the standings section is replaced by the postseason picture.

**Series**:
A best-of-seven playoff pairing between two teams. Its state (game number, who leads, by how much) is shown on every postseason matchup and result.

**Postseason picture**:
The list of play-in games and playoff series with their current state, shown instead of the standings during the postseason.

**Leaderboard**:
The season-to-date top 25 qualified players ranked by PIE (NBA.com's Player Impact Estimate), with points, rebounds, assists, and true shooting. The digest carries one.
_Avoid_: Stats, leaders data

### Plumbing

**Source**:
An external system the digest is built from. ESPN is the primary source; NBA.com is the enrichment source.

**Enrichment**:
Data only NBA.com provides (local TV and streaming, team ratings, the leaderboard). Its absence degrades the digest but never stops a send.

**Source snapshot**:
The most recent successful raw response from a source, kept on disk. When a source fails at send time, the digest is built from its snapshot and says so.
_Avoid_: Cache, blob

**Transport**:
A way of delivering a digest. Resend is the primary transport; SMTP is the fallback.
_Avoid_: Email service, provider
