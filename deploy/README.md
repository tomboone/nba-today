# Deploying nba-today on kumadrome

nba-today runs as one container on kumadrome, with its own Watchtower beside it pulling new images from GHCR.
Docker and the Compose plugin are already installed there, and mlb-today runs on the same host. Besides this
GitHub repository, the only outside accounts involved are [Resend](https://resend.com) (email, reusing mlb-today's
verified domain) and [healthchecks.io](https://healthchecks.io) (alerting).

Images are published by `.github/workflows/image.yml` on every push to `main`:

| Tag | Use |
|---|---|
| `latest` | what kumadrome runs |
| `sha-<7>` | pinning or rolling back to one commit |

Commands below run on kumadrome (`ssh kumadrome`) unless they start with `ssh`. A step that needs `sudo` is written
as `ssh -t kumadrome 'sudo …'` so it can prompt for the password.

## 1. Make sure kumadrome can pull the image

Watchtower pulls anonymously, so the `ghcr.io/tomboone/nba-today` package must be public. After the first image
publish: your GitHub profile → Packages → `nba-today` → Package settings → Change visibility → Public. Check it
from kumadrome with `docker pull ghcr.io/tomboone/nba-today:latest`.

## 2. Fetch the deploy files

Only `deploy/` is needed. A sparse clone fetches that directory (plus the handful of top-level files git always
includes) without the source tree:

```sh
git clone --filter=blob:none --sparse --branch main https://github.com/tomboone/nba-today.git ~/nba-today
cd ~/nba-today
git sparse-checkout set deploy
cd deploy
```

Every command from here on runs in `~/nba-today/deploy/`. `git pull` picks up later changes to these files.

Create the data directory the container writes snapshots, state, and dry-run output to. The container runs as a
non-root user with UID/GID 999, so the directory must be owned by it, or Docker creates it root-owned and the app
cannot write:

```sh
mkdir -p ~/nba-today/deploy/data
ssh -t kumadrome 'sudo chown 999:999 ~/nba-today/deploy/data'
```

## 3. Create the healthchecks.io check

1. In healthchecks.io, **Add Check**. Set **Period** to 1 day and **Grace** to 12 hours. NBA send times swing
   further than MLB's: a noon holiday tip followed by a 7:30 PM tip the next day is 31.5 hours between pings.
2. Under **Integrations**, make sure at least one notification channel (email is the default) is enabled.
3. Copy the check's ping URL (`https://hc-ping.com/<uuid>`) for `HEALTHCHECKS_URL`.

The app pings after each successful send and pings `/fail` when a send fails even after its retry. Only send days
ping, so pause the check over the All-Star break and the off-season to avoid false alarms.

## 4. Configure `.env`

```sh
cp .env.example .env
chmod 600 .env
```

Edit `.env`. At minimum set `RECIPIENTS`, `RESEND_API_KEY`, `RESEND_FROM`, and `HEALTHCHECKS_URL`.
`RESEND_FROM` must be an address on the domain already verified for mlb-today, e.g.
`NBA Today <nba@tomboone.com>`. The SMTP variables enable the SMTP fallback transport (Fastmail); leave them blank
to run Resend alone. Every variable and its default is documented in `.env.example`.

For a first run, set `DRY_RUN=true`: digests are written to `data/outbox/<date>.html` instead of emailed, so you
can check a few days' output before going live.

## 5. Start it

```sh
docker compose config -q   # validates the compose file and .env; prints nothing on success
docker compose up -d
docker compose ps
```

`docker compose ps` shows both services running. `nba-today` reports `health: starting` until its first health
check, about 5 minutes after start, then `healthy` (the scheduler plans the day at boot).

## Day-to-day

**Logs.** The app logs structured JSON to stdout:

```sh
docker compose logs -f nba-today
docker compose logs -f watchtower    # image updates
```

**Preview a digest** without sending it. Write into `/data` (the only directory the container can write), which is
`./data` on the host:

```sh
docker compose exec nba-today nba-today preview --out /data/digest.html
docker compose exec nba-today nba-today preview --date 2026-12-25 --out /data/digest.html
docker compose exec nba-today nba-today preview --from-snapshots --out /data/digest.html   # no network
```

Each writes `data/digest.html` and `data/digest.txt`; copy them off kumadrome to view, e.g.
`scp kumadrome:nba-today/deploy/data/digest.html .`.

**Send one now:** `docker compose exec nba-today nba-today send` (add `--dry-run` to write to `data/outbox/`
instead). A manual send does not record the last-sent marker, so the scheduler still sends that day's digest at its
usual time; use `--dry-run` or `preview` when you only want to look.

**Health:** `docker compose exec nba-today nba-today health; echo $?` exits 0 when the scheduler has been active
in the last 26 hours.

**Updates** are automatic: Watchtower checks GHCR every 5 minutes and restarts `nba-today` on the new image,
removing the old one. It runs with `--scope nba-today`, so it only touches containers labelled with that scope.
To update by hand: `docker compose pull && docker compose up -d`.

mlb-today's Watchtower on the same host must be scoped too (NEU-1797) before this one starts. An unscoped
Watchtower updates every enabled container whatever its scope, and when it starts it stops every other Watchtower
on the host ([Watchtower docs](https://containrrr.dev/watchtower/running-multiple-instances/)).

**Roll back** by pinning a commit: change the `image` tag in `docker-compose.yml` to `sha-<7>` and run
`docker compose up -d`. Watchtower keeps following that tag, which never moves, so updates stop until you switch
back.
