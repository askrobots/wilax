# wilax slim plan: mailcow on a 1GB / 1 vCPU droplet

Status: analysis from reading the repo (docker-compose.yml, generate_config.sh, data/Dockerfiles, data/conf, data/web). Nothing was run. **All RAM numbers are estimates from general knowledge of these programs and the shipped config, not measurements.** Measure with `docker stats --no-stream` and `free -m` before trusting any of it.

Companion file: `docker-compose.slim.override.yml` (mem_limit caps, MariaDB/memcached args, PHP opcache args; header lists required mailcow.conf values).

## 1. Per-service table

"Idle RAM" = rough resident memory of an idle, default-configured container with a handful of mailboxes.

| Service | Purpose | Idle RAM (est.) | If removed | Config-only? | Order |
|---|---|---|---|---|---|
| clamd-mailcow | ClamAV antivirus + freshclam | 1.0-1.6 GB (signature DB loaded) | No AV scanning; rspamd antivirus module logs errors unless disabled; watchdog check skipped by SKIP_CLAMD | Yes: `SKIP_CLAMD=y` (container sleeps; generate_config.sh already defaults y at <=2.5GB RAM) | 1 |
| sogo-mailcow | Webmail, calendar, CardDAV/ActiveSync | 400-700 MB (WOWorkersCount=20 in data/conf/sogo/sogo.conf), +memcached | No webmail/groupware/ActiveSync; nginx drops SOGo locations (bootstrap.py honors SKIP_SOGO); admin UI "SOGo" SSO link breaks; `update_sogo_static_view` becomes a no-op | Yes: `SKIP_SOGO=y`. Alternative: keep, set WOWorkersCount=2-3 (still ~250 MB+) | 2 |
| olefy-mailcow | Python oletools proxy for rspamd macro scanning | 30-60 MB | No Office-macro scanning | Yes: `SKIP_OLEFY=y` (rspamd entrypoint disables olefy config) | 1 |
| memcached-mailcow | SOGo session cache; web flushes it (`flush_memcached()` in functions.inc.php) | 5-10 MB (cap `-m 16` in override) | Unused when SOGo is off; PHP code uses `Memcached` class guarded by try/catch? **Unverified**: confirm before scaling to 0 | Cap by override; removal needs a code check | 6 |
| dovecot FTS (Flatcurve) | Full-text search | 100-500 MB during indexing (FTS_HEAP=512, FTS_PROCS=3) | No server-side body search | Yes: `SKIP_FTS=y` (generate_config.sh emits n; compose default is y) | 1 |
| watchdog-mailcow | Bash health checks, auto-restart, notifications, writes to redis | 50-100 MB, bursty CPU on 1 vCPU | No auto-heal, no alert mail, admin "Logs/Watchdog" empty | Yes: `USE_WATCHDOG=n` (watchdog.sh honors it); caution, still a container | 5 |
| ofelia-mailcow | Docker-label cron (SOGo/dovecot jobs, keycloak/ldap sync) | 10-20 MB | Dovecot maintenance crons (quota/maildir GC jobs, sogo expunge) stop | Cannot drop safely; keep. Replace later by host cron | 9 |
| dockerapi-mailcow | Python REST proxy to docker.sock for admin UI (restart, logs, vmail df) | 50-90 MB | Admin UI cannot show container status/restart/df; php `functions.docker.inc.php` calls fail; passwords/`prerequisites.inc.php` refs. Watchdog and other containers call it too | No: needs code change (stub or remove calls). | 7 (replace) |
| netfilter-mailcow | Fail2ban-like iptables bans (privileged, host net) | 40-70 MB | No auto-banning of brute-force | No flag. Can remove from stack but compose `depends_on` (mysql, redis, dovecot) lists it; override with `deploy.replicas:0` also fails dependency order in some compose versions. Replace by host fail2ban/ufw | 8 |
| unbound-mailcow | Local validating DNS resolver (rspamd RBLs need non-public-resolver) | 20-50 MB | RBL lookups against public resolvers get blocked/limited by blacklist providers; DNSSEC for tlspol/DANE lost | Not removable by flag (SKIP_UNBOUND_HEALTHCHECK only skips health). Keep | 10 (keep) |
| postfix-tlspol-mailcow | MTA-STS/DANE policy daemon (Go) | 15-30 MB | Outbound opportunistic TLS only; postfix main.cf refers to it | Needs main.cf edits; small saving, defer | 10 |
| acme-mailcow | Let's Encrypt via acme.sh | 30-50 MB | No auto certs (you must supply certs in data/assets/ssl) | Yes: `SKIP_LETS_ENCRYPT=y` (container stays, sleeps), only if providing your own certs | 4 (optional) |
| nginx-mailcow | Reverse proxy for UI/autoconfig/SOGo | 15-30 MB | No web UI | No | keep |
| php-fpm-mailcow | Admin UI + API + dovecot auth (mailcowauth.php) + rspamd dynmaps | 150-300 MB default (15 system + 50 web max children, 128MB opcache) | Everything admin; dovecot auth passdb partly depends on it | Tune only (section 2) | tune |
| redis-mailcow | Sessions, rspamd stats/Bayes, ratelimits, logs | 10-60 MB (grows with Bayes/logs) | rspamd, admin, dovecot quota notify break | Tune only | keep |
| mysql-mailcow (MariaDB 10.11) | Config DB | 100-200 MB; data/conf/mysql/my.cnf already has pool=24M, perf_schema off | All | Tune only; later replace with SQLite/Postgres (large code change: PDO usage throughout data/web, dovecot SQL, postfix maps, sogo) | tune; replace 4th |
| rspamd-mailcow | Spam filter (proxy/milter, normal, controller, fuzzy workers) | 150-300 MB (Lua + Hyperscan maps, redis-backed) | Spam filtering, DKIM signing (mailcow signs in rspamd), milter ratelimits, quarantine | Tune only; DKIM dependency makes removal a code change | tune |
| dovecot-mailcow | IMAP/POP/LMTP/sieve/doveadm, plus imapsync, cron scripts | 80-200 MB (imap-login process_min_avail, vsz_limit are virtual) | Everything | Tune only | tune |
| postfix-mailcow | MTA (postscreen, smtpd, etc.) | 40-90 MB | Everything | Tune only | tune |

Assumptions flagged: figures for php-fpm, rspamd, dovecot depend heavily on activity and message sizes. Idle numbers are low-confidence (plus or minus 50%).

## 2. Tuning knobs that reduce memory

All paths relative to repo root.

### MariaDB, `data/conf/mysql/my.cnf`
- `innodb_buffer_pool_size = 24M` already shipped. Leave at 24-32M for a few mailboxes; do not raise.
- `max-connections = 550` -> 50-80 (per-connection buffers; override passes 60).
- `max_heap_table_size = 48M`, `tmp_table_size = 24M` -> 16M.
- `sort_buffer_size = 2M` -> 512K; `read_buffer_size=192K`, `read_rnd_buffer_size=256K` fine.
- `thread_cache_size = 8` -> 4; add `table_open_cache=200`, `innodb_log_buffer_size=4M`, `aria_pagecache_buffer_size=8M` (Aria is used for temp tables), `innodb_log_file_size` leave default.
- `performance_schema = 0` already set (saves ~100MB vs default).
The override applies the first set via `command:` args; edit my.cnf to make permanent (args also win over my.cnf).

### PHP-FPM, `data/conf/phpfpm/php-fpm.d/pools.conf` (not overridable from compose)
- `[web-worker]`: `pm.max_children=50, start_servers=10, min_spare=10, max_spare=15` -> `pm=ondemand`, `pm.max_children=4`, `pm.process_idle_timeout=20s` (or dynamic with start 1, min 1, max_spare 2).
- `[system-worker]`: `max_children=15, start 2, spare 2-4` -> `pm=ondemand, max_children=3`. This pool serves dovecot auth callbacks and rspamd dynmaps, so keep >=3 or logins queue.
- `memory_limit` 512M/256M is per request; lower web-worker to 128M (large uploads/API bulk calls may fail).
- `data/conf/phpfpm/php-conf.d/opcache-recommended.ini`: `opcache.memory_consumption=128` -> 48, `interned_strings_buffer=16` -> 8 (override passes these via `-d`; php-fpm `-d` sets global ini, fine).
- Saves roughly 100-200 MB vs default under load; at idle ondemand workers cost zero.

### Redis, `data/conf/redis/redis-conf.sh`
No maxmemory set. Add `maxmemory 64mb` and `maxmemory-policy volatile-lru`? **Risk:** rspamd Bayes/fuzzy keys and sessions without TTL would hit OOM errors under `noeviction`; use `allkeys-lru` only if you accept Bayes loss. Prefer `maxmemory 96mb` + `noeviction` and monitor. Also `save ""` removes RDB fork copy-on-write spikes (but loses persistence across restarts; acceptable for logs, not for Bayes). Also `LOG_LINES=1000` in mailcow.conf shrinks log lists.

### Dovecot, `data/conf/dovecot/dovecot.conf`
- `service imap-login`: `process_min_avail = 2` -> 1; `service_count=1` with `process_limit=10000` means a fork per login; consider `service_count=0`/`client_limit` high-performance mode only if memory allows (fewer procs vs more heap per proc; uncertain net effect).
- `service managesieve-login` and `pop3-login`: `process_min_avail` 2/1 -> 0/0 (disable POP3 listeners if unused).
- `default_vsz_limit = 1024M` and per-service `vsz_limit = 1G/2G` are virtual limits, not RSS; lowering to 256M is safe for most services but keep 512M+ for `imap` with large mailboxes.
- `default_client_limit = 10400`, `service imap`: add `process_limit = 50` (default 1024; each IMAP session = one process of ~5-15MB).
- `auth_cache_size = 10M` -> 2M. `mail_max_userip_connections = 500` -> 20.
- `mail_plugins`: remove `quota`? no; leave. With `SKIP_FTS=y` flatcurve is not loaded.

### Rspamd, `data/conf/rspamd/override.d/*.inc` and `local.d/`
- `worker-normal.inc`: `count = 1` already. Good.
- `worker-proxy.inc`: add `count = 1`.
- `worker-fuzzy.inc`: disable fuzzy worker or leave; it holds a sqlite db (low).
- `local.d/` modules: disable `neural`, `asn` (asn.conf), `phishing`, `spamassassin.conf` (if SA rules are loaded, that is the heaviest Lua map set), `reputation`, `mx_check`, `antivirus` when clamd off; each saves a few to tens of MB and CPU.
- Keep `dkim_signing`, `ratelimit`, `statistic`.
- With SKIP_CLAMD=y, also set `enabled = false;` in `local.d/antivirus.conf` (otherwise it logs connection errors and may delay scans; check entrypoint behavior for your version -- not verified).

### SOGo (only if kept), `data/conf/sogo/sogo.conf`
- `WOWorkersCount = "20"` -> 2 or 3; add `SxVMemLimit = 384;` (verify key support in SOGo 5.12).

### ClamAV (if kept at all) `data/conf/clamav/clamd.conf`
- `MaxThreads 10` -> 1; `ConcurrentDatabaseReload no` already. Resident set is dominated by the signature DB (~1GB+). Not viable at 1GB; alternatives: clamd on another host (TCP) or rspamd external scanners.

### Host
- 2GB swapfile, `vm.swappiness=10`, `vm.overcommit_memory=1` for Redis. Docker daemon + containerd ~100-150MB, OS ~100MB. On a 1GB droplet, ~900MB is available to userland.
- Disable `dockerapi`/`watchdog` log verbosity, `LOG_LINES=1000`.

## 3. Will config-only fit in 1GB?

Estimated sum of the slim set (SKIP_CLAMD/OLEFY/SOGO/FTS=y, tuning above, watchdog kept):

| Group | Estimate |
|---|---|
| mariadb 100, redis 25, rspamd 200, dovecot 90, postfix 50, php-fpm 90, nginx 20 | ~575 MB |
| unbound 30, tlspol 20, acme 35, netfilter 50, watchdog 70, dockerapi 60, ofelia 15, memcached 6, olefy/sogo/clamd sleep stubs ~15 | ~300 MB |
| Docker daemon + OS + page cache floor | ~250 MB |
| **Total** | **~1.1 GB** (plausible range 0.85-1.4 GB) |

Honest verdict: config-only is **borderline**. With 2GB swap it will probably boot and serve a few mailboxes, but it will likely swap during rspamd scans, imap bursts or admin UI use, and cold start (all containers coming up at once on 1 vCPU) may OOM or take several minutes. Without SOGo and ClamAV it is a headless mail server plus admin panel. It is not comfortable. Setting `USE_WATCHDOG=n` and dropping optional services (tlspol, acme with own certs) gets to roughly 0.9-1.0 GB, still marginal. A 2GB droplet ($12) would be the safe config-only target; 1GB needs replacements.

## 4. Recommended order (config first, then replacement)

1. Config-only: `SKIP_CLAMD=y SKIP_OLEFY=y SKIP_SOGO=y SKIP_FTS=y`, apply override, swap, pools.conf and dovecot limits, rspamd module trimming. Measure.
2. Stop the sleeping stubs: they still occupy a container (few MB each) and gate `depends_on`. Remove clamd/olefy/sogo/memcached services from the compose via a code change (nginx/ofelia/rspamd `depends_on` lists reference clamd and sogo, so edit compose, not just override).
3. Replace `dockerapi` + `watchdog` with a tiny single process (or host systemd + cron): dockerapi is only reached from `data/web/inc/functions.docker.inc.php` and watchdog; the admin pages would need those calls stubbed. Saves ~120-170 MB.
4. Replace netfilter with host fail2ban/nftables (~50 MB, and removes privileged container).
5. Merge small Go/Python helpers: fold acme into host certbot/lego timer, `postfix-tlspol` into postfix config (or drop MTA-STS/DANE).
6. Largest structural win: swap MariaDB for SQLite or an embedded DB (needs changes in `data/web/inc/init_db.inc.php`, `functions.inc.php` PDO usage, dovecot passdb/userdb SQL, postfix SQL maps, `_sogo_static_view`). Large change, ~100 MB saved; high risk.
7. Rspamd -> lighter filter (e.g. only DKIM/SPF/DMARC via opendkim/opendmarc + simple greylisting) only if rspamd remains the biggest; loses mailcow's UI-integrated spam features and DKIM management in the admin panel, so it is a deep code change.
8. php-fpm admin: consider serving UI through php-fpm ondemand or a single static binary rewrite; largest rewrite effort, defer.

## 5. Top risks
- Cold-start and OOM-kill loops: hard `mem_limit` plus `restart: always` can thrash; some caps (rspamd 300m, php 256m) may be too tight under real load.
- Disabling watchdog/netfilter/Clam removes self-healing, brute-force bans and AV: security posture drops. Bind-mounted config files edits (pools.conf, dovecot.conf) are upstream-merge conflicts for the fork.
- Unverified assumptions: whether web admin tolerates missing memcached/dockerapi/clamd without errors, whether `command:` overrides on mariadb/memcached merge as expected on your compose version, and rspamd antivirus module behavior with SKIP_CLAMD=y.
