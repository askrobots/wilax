# mailwon handoff (for a fresh Claude Code session)

## What and why
mailwon is a fork of mailcow-dockerized aiming at a Mailcow-compatible stack that runs on a 1GB RAM / 1 vCPU, $7/month DigitalOcean droplet. askrobots.com hosts email for users: the q9 app (`~/IdeaProjects/q9`) drives Mailcow through its API (`email_hosting/mailcow_client.py`, class `MailcowClient`) and mirrors mail into Django via IMAP polling. Stock Mailcow needs swap on 1GB and is slow.

## State of the fork
- Repo: `/Volumes/T9/mailcow` (git). Local commits are NOT pushed to github.com/askrobots/mailwon. The `upstream` remote (mailcow) is kept. Check `git status`/`git log` first; do not assume anything is pushed.

## File map
- `docs/slim/PLAN.md`: component-by-component RAM analysis and slimming plan.
- `docs/slim/results.md`, `docs/slim/FINDINGS.md`, `docs/slim/slim-final/`, `docs/slim/conf/`: results, findings and tuned configs written this session (verify they exist; see Results below).
- `docs/slim/provision-droplet.sh`: create/status/ssh/smoke/destroy the test droplet.
- `docs/slim/bootstrap-remote.sh`: runs on the droplet (docker, swap, firewall, mailcow.conf, stack up).
- `docker-compose.slim.override.yml`: memory-tuning override, used as `-f docker-compose.yml -f docker-compose.slim.override.yml`.
- `tests/test_static.py`: static checks (no Docker needed).
- `helper-scripts/dev_tests/smoke.py`: end-to-end smoke test (API, SMTP 587/25, IMAPS, DKIM, rspamd headers); env documented in its docstring.

## Test droplet (bills hourly, about $0.0104/hr)
```
docs/slim/provision-droplet.sh create [name]   # default mailwon-test2; nyc1, s-1vcpu-1gb-amd, ubuntu 24.04, ssh key 38944259, tag mailwon-test
docs/slim/provision-droplet.sh status | ssh | smoke
docs/slim/provision-droplet.sh destroy [--yes] # deletes only droplets tagged mailwon-test, then lists the rest
```
Cleanup rule: destroy the droplet at the end of EVERY session. Never touch droplets `ubuntu-s-1vcpu-1gb-amd-nyc1` and `nc`, or the user's production Mailcow. The older baseline droplet `mailwon-test` (id 611712711) may still exist if not yet deleted; check `doctl compute droplet list`.

## Measured facts so far (stock-ish stack, 1GB + 2GB swap)
- All 18 containers up ~5 min after `up -d` (pull 2m19s), no OOM kills.
- With SOGo: 748MB used of 961MB, ~980MB swap used, load 0.16-0.45, web and /SOGo/ HTTP 200.
- SKIP_SOGO=y: 727MB used, 686MB swap. SKIP flags barely help because stub containers (sogo, clamd, olefy) still run.
- `docker stats` is unreliable under swap (pages swapped out); use `free -m`.
- unbound flapped once when an early iptables rule blocked return traffic.

## Unverified
- Everything in `bootstrap-remote.sh` and `provision-droplet.sh` has only been syntax-checked (`bash -n`), never run. In particular: generate_config.sh non-interactive run via a throwaway git repo, the API-key SQL insert into table `api`, and the healthy-wait loop.
- Whether the slim override's memory limits/commands merge and work; whether admin UI tolerates missing dockerapi/memcached/clamd; rspamd behaviour with SKIP_CLAMD=y.
- Behaviour under real mail load and a real domain.

## Next steps (priority order)
1. Apply tuned configs (`docs/slim/conf/`, `slim-final/`, override) on a recreated droplet; measure with `free -m` and run `smoke`.
2. Replace the engine behind a `MailwonClient` exposing the same ~20 methods as `MailcowClient`, so q9 can switch without other changes.
3. Direct delivery to the app (e.g. LMTP/pipe/webhook) instead of IMAP polling.
4. Pilot one throwaway domain end to end.
5. Push the fork to github.com/askrobots/mailwon (ask the user first).

## Results from this session
Date: 2026-10-22. Details and raw numbers: `docs/slim/results.md`; reproduce steps: `docs/slim/slim-final/REPRODUCE.md`.

**Measured on a real 1GB/1vCPU droplet (2GB swap, Compose v5.6.1), localhost only:**
- A headless Mailcow of **11 containers** (down from 18) passes `helper-scripts/dev_tests/smoke.py` **11/11** (re-run independently by the coordinator at the end: 2.1s total). Idle: ~660-680MB used of 961MB, ~510-530MB swap in use, ~290MB available, load ~0.05.
- Removed for real (compose `profiles: ["removed"]` + `depends_on: !override`): sogo, clamd, olefy, memcached, watchdog, netfilter, postfix-tlspol. acme also removes cleanly but only when certs already exist, so it is kept as a stub.
- It **fits only because of swap.** Removing stubs barely lowered RAM (stubs were already swapped out). Bursts cause 1-second swap spikes of 17-30MB/s; API latency 0.14s warm, ~0.8s when swapped out.
- php-fpm is the clearest tuning win (137MB -> 41-72MB, `ondemand` pool). rspamd (~260MB), ofelia (~95-170MB; needs mem_limit >= 96m or it is OOM-killed), dockerapi (~70MB) and mariadb (~60MB) are what is left.

**Bugs found only by running it (all fixed in the repo):** the draft override capped ofelia at 32m (OOM-killed); smoke.py's DKIM step failed because Mailcow auto-creates the key on domain add; authenticated-submission mail carries no X-Spamd-Result (the check uses the port-25 message); tests/test_static.py now accepts `!override` YAML tags.

**Not tested:** outbound mail (no DNS), so removing tlspol (loses MTA-STS/DANE) is unverified; real load; cold-start time; re-enabling SOGo/ClamAV; TLS from remote clients; removing netfilter/watchdog drops brute-force bans and auto-heal (acceptable only for a private test box).

**Provisioning validated end to end (2026-10-22):** `docs/slim/provision-droplet.sh create` built a fresh droplet from nothing, `bootstrap-remote.sh` brought up the 11-container slim stack, and `provision-droplet.sh smoke` passed 11/11; `destroy --yes` then deleted only the tagged droplet. Fresh stack: 11 containers, 717MB used, 477MB swap, 244MB available, ~8 minutes after create. Running it found and fixed four bugs: (1) git "dubious ownership" because rsync keeps the Mac uid (now `chown -R root:root` + safe.directory); (2) `tr | head` under `pipefail` killed the script with SIGPIPE (API key now `openssl rand -hex 16`); (3) the `smoke` command re-ran `rsync --delete`, which deleted `data/assets/ssl` and the slim `data/conf` files and broke Postfix TLS (now it copies only smoke.py; never re-run sync_tree on a live box); (4) stale `SMOKE_SKIP` defaults. Also added to the bootstrap: loopback bindings, USE_WATCHDOG=n, slim-config.patch, an explicit snake-oil cert step. Known quirk: containers keep a stale bind mount if a host directory is deleted and recreated, so `up -d --force-recreate` them.

**Droplet state:** both test droplets (`mailwon-test` 611712711 and the validation droplet `mailwon-test2` 611727742) were destroyed at the end of this session, as required; `doctl compute droplet list` shows only the user's own `ubuntu-s-1vcpu-1gb-amd-nyc1` and `nc`. Nothing of ours is running.

**Suggested next steps:** (1) provisioning is validated, so start from `provision-droplet.sh create`; (2) trim rspamd module by module and measure; (3) replace ofelia with host cron and make dockerapi optional (needs a change in data/web/inc/prerequisites.inc.php, which currently returns HTTP 500 for the whole PHP layer without it); (4) decide between a 2GB droplet (about $5 more) and a deeper rewrite; (5) the real goal: a `MailwonClient` with the same ~20 methods as `email_hosting/mailcow_client.py`, then direct delivery to the app instead of IMAP polling; (6) test outbound mail before trusting it with real domains.
