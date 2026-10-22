# Slim findings (offline code read; nothing executed; "UNVERIFIED" = not provable from the repo)

Compose line refs are docker-compose.yml. Verdict key: A = safe, no code change; B = compose-only tweak (drop depends_on entries / service); C = needs a patch or must stay.

## 1. What breaks if the container is truly absent

| Service | Compose depends_on that would break `up` | Runtime references | Verdict |
|---|---|---|---|
| sogo | nginx depends_on sogo (:420), ofelia depends_on sogo (:650) | nginx template wraps all SOGo locations in `{% if not SKIP_SOGO %}` (sites-default.conf.j2:171); bootstrap.py reads SKIP_SOGO (:52). PHP calls `docker('post','sogo-mailcow','restart')` only if `restart_sogo` was requested (functions.mailbox.inc.php:516,674) and `exec` on mailbox rename (:3699, result ignored; dockerapi error UNVERIFIED but not checked). Ofelia sogo jobs are labels on the sogo container (:238-246) and vanish with it. Dovecot still writes /etc/sogo creds (dovecot docker-entrypoint.sh:177,216) harmless. | B: drop 2 depends_on entries + set SKIP_SOGO=y (nginx must be told, else dead proxy_pass blocks) |
| clamd | rspamd depends_on clamd (:91) | rspamd local.d/antivirus.conf:9 `servers = "clamd:3310"` is always loaded; rspamd entrypoint has NO SKIP_CLAMD handling (grep: only clamd.sh/healthcheck.sh/watchdog.sh:917/dashboard.php:9 read it). Absent host = unresolved name, error log per scan (UNVERIFIED rspamd behavior). Composites CLAMD_* (composites.conf:76-106) just reference missing symbols. | B + install conf/.../override.d/antivirus.conf (enabled=false) |
| olefy | none | rspamd entrypoint deletes external_services.conf when SKIP_OLEFY=y (docker-entrypoint.sh:84-87); watchdog skips olefy check (watchdog.sh:1039). Needs SKIP_OLEFY=y still set on rspamd. | A (with SKIP_OLEFY=y) |
| memcached | none (no one lists it) | Only PHP `flush_memcached()` (functions.inc.php:450-458) and init_db.inc.php:1576, both inside try/catch(Exception). `new Memcached` throws Error (not caught) only if the PHP ext were missing: UNVERIFIED. Unresolvable host timeout behavior UNVERIFIED. SOGo is the real consumer. | A/B: remove; test by adding a mailbox (flush runs at functions.inc.php:1055) |
| watchdog | none depend on it | Nothing in web/conf references it except admin log views (redis-fed). Its own depends_on (:540-545) are on others, harmless. Compose default USE_WATCHDOG=n (:593 region). | A: remove |
| ofelia | none | Runs ALL cron: dovecot trim_logs, quarantine_notify, clean_q_aged, maildir_gc, imapsync_runner, sa-rules, fts, repl_health (:316-329) and phpfpm keycloak/ldap sync (:191-196). Without it redis log lists are never trimmed (memory growth), quarantine never aged/notified (product reads quarantine). Baseline saw ofelia up to ~196MB (per caller). | C: keep, or replace with host cron `docker exec` for trim_logs.sh + clean_q_aged.sh + maildir_gc.sh |
| netfilter | mysql (:23), redis (:52), dovecot (:258) depend on it | Admin fail2ban code calls `docker('post','netfilter-mailcow','restart')` (functions.fail2ban.inc.php:112) only when editing f2b settings. Bans live in redis; no enforcement without it. It is `privileged`, `network_mode: host` (:508-509 area). It also owns the isolation iptables rule (DISABLE_NETFILTER_ISOLATION_RULE env :524 region; effect UNVERIFIED). | B: drop 3 depends_on; replace by host ufw/fail2ban |
| acme | watchdog depends_on acme (:544) | With SKIP_LETS_ENCRYPT=y container just `sleep 365d` (acme.sh:60-64) so it is ~free. It waits for dockerapi (acme.sh:67). If absent AND certs not supplied in data/assets/ssl, nothing renews (who creates the initial self-signed cert is UNVERIFIED). | B: keep stub with SKIP_LETS_ENCRYPT=y, or remove and drop watchdog dep |
| postfix-tlspol | postfix depends_on tlspol (:348) | main.cf:153 `smtp_tls_policy_maps = ... socketmap:inet:postfix-tlspol:8642:QUERYwithTLSRPT`; unreachable socketmap = lookup failures, likely outbound deferrals (UNVERIFIED but standard postfix behavior). watchdog check only if enabled (watchdog.sh:453-464,957). | B + postfix extra.cf override of smtp_tls_policy_maps (see conf/.../postfix/extra.cf); loses MTA-STS/DANE |
| dockerapi | acme/watchdog wait for it | **Hard dependency of the PHP layer**: prerequisites.inc.php:108-115 `fsockopen("tcp://dockerapi",443)` and exits with HTTP 500 before ANY page/API (required by json_api.php:5, index.php:2, quarantine.php:2, qhandler.php:3 ...). Also acme (acme.sh:67), watchdog, reload-configurations.sh, sa-rules.sh:28, mailq/ACL/maildir_gc calls (functions.mailq.inc.php:49; mailbox.inc.php:3588,3687,5755 etc.), vmail df in json_api.php:1486. Mount is docker.sock :ro. | C: MUST STAY (the API the product uses 500s without it). Removal needs patching prerequisites.inc.php (fsockopen block) and every docker() caller |
| unbound | mysql (:22), clamd, postfix (:346 healthy), tlspol, acme (:466) | Every service has `dns: 172.22.1.254` (e.g. :74,:113,:261,:399,:424); absent = no DNS in rspamd/postfix/dovecot/nginx. rspamd options `dns { enable_dnssec = true; }` (options.inc:1-3). | C: must stay |

## 2. Compose mechanism for removal (override file)

- depends_on from list syntax is MERGED across files (union), so an override cannot delete a base dependency by omission. Options:
  - `depends_on: !override [a, b]` or `!reset` on dependents: needs Compose >= 2.24.0 (Jan 2024; from my knowledge, UNVERIFIED version). Ubuntu 24.04 `docker-compose-v2` apt package is 2.24.x (UNVERIFIED exact); Docker's own apt repo plugin is newer. Check `docker compose version` first.
  - long-form `depends_on: {sogo-mailcow: {condition: service_started, required: false}}` merges with the list form; `required: false` needs >= 2.20.0. Use as fallback; it tolerates a missing/disabled dependency.
  - `profiles: ["disabled"]` on the unwanted service (supported since Compose v2 start): service is skipped by `up`; a plain depends_on on it from an active service errors, so combine with !override/required:false on dependents.
  - `deploy.replicas: 0`: UNRELIABLE here (depends_on still resolves; recommend against). 
  - `!reset null` on a whole service to delete it: >= 2.24, UNVERIFIED for whole services; profiles is safer.
- Recommended fragment (for agent A; I did not touch the override file):
```yaml
services:
  sogo-mailcow:      {profiles: ["disabled"]}
  clamd-mailcow:     {profiles: ["disabled"]}
  olefy-mailcow:     {profiles: ["disabled"]}
  memcached-mailcow: {profiles: ["disabled"]}
  watchdog-mailcow:  {profiles: ["disabled"]}
  nginx-mailcow:   {depends_on: !override [redis-mailcow, php-fpm-mailcow, rspamd-mailcow]}
  rspamd-mailcow:  {depends_on: !override [dovecot-mailcow]}
  ofelia-mailcow:  {depends_on: !override [dovecot-mailcow]}
  # only if netfilter is also removed:
  # mysql-mailcow: {depends_on: !override [unbound-mailcow]}
  # redis-mailcow: {depends_on: !reset []}
  # dovecot-mailcow: {depends_on: !override [mysql-mailcow, redis-mailcow]}
```
  Also keep SKIP_SOGO=y, SKIP_CLAMD=y, SKIP_OLEFY=y so env-driven code paths stay consistent. Note: `up -d` after the fact leaves old stub containers running; use `docker compose up -d --remove-orphans` or `rm -sf`.

## 3. Memory knobs and overridability

| Knob | Mechanism / exact path | Overridable without editing tracked file? | In conf/ |
|---|---|---|---|
| rspamd worker counts | normal/controller/fuzzy already `count = 1` (override.d/worker-normal.inc:3, worker-controller.inc:2, worker-fuzzy.inc:6); proxy count not set. Hooks `worker-*.custom.inc` included at priority 30 (worker-proxy.inc, worker-normal.inc, worker-controller.inc); `logging.custom.inc` (logging.inc:5) | Yes (new .custom.inc files) | worker-proxy.custom.inc |
| rspamd module disable | `override.d/<module>.conf` with `enabled = false;` (override.d dir is mounted :103; gitignored .gitignore:45) | Yes, new files. Disabling a module that has a TRACKED local.d file (asn, phishing, reputation, mx_check, spamassassin, fuzzy_check) still works via override.d but UNVERIFIED precedence; local.d is gitignored (:44) yet files are tracked | neural.conf, antivirus.conf |
| rspamd options (task_timeout, dns) | local.d/options.inc is tracked | No (edit tracked) | not provided |
| Clickhouse/other heavy modules | not configured in repo (no local.d/clickhouse*) so already off (grep of local.d list) | n/a | |
| ratelimit / redis storage | override.d/ratelimit.conf tracked | No | |
| php-fpm pm | tracked data/conf/phpfpm/php-fpm.d/pools.conf mounted as single file (:~144 `z-pools.conf`) | Only by replacing the mount in the override yml (same target path) | pools.slim.conf |
| opcache | tracked php-conf.d/opcache-recommended.ini (consumption 128, interned 16) | Yes via `php-fpm -d ...` in override command (already done); `-d` beats ini | |
| dovecot | `data/conf/dovecot/extra.conf` included at dovecot.conf:306; gitignored. default_* globals at :310-311 come after the include so cannot be overridden there; per-service settings can | Yes (service blocks, auth_cache_size, mail_max_userip_connections) | extra.conf |
| postfix | `data/conf/postfix/extra.cf` appended to main.cf (postfix.sh:488-491); master.cf maxproc columns are tracked (postscreen maxproc 1, master.cf:2-3) | main.cf params yes (default_process_limit); master.cf no | extra.cf |
| mariadb | `data/conf/mysql/*.cnf` dir mounted (:28); tracked my.cnf already: pool 24M (:~14), max-connections 550, tmp 24M, perf_schema 0 | Yes, new slim.cnf (sorts after my.cnf) or `command:` args | slim.cnf |
| redis | redis-conf.sh tracked, entrypoint script ignores compose `command` args (script ends with `exec redis-server /redis.conf`), no maxmemory set | Only by remounting a replacement script, or runtime `CONFIG SET` | redis-slim.sh |

## 4. Adversarial review of docker-compose.slim.override.yml

| Item | Finding |
|---|---|
| ofelia 32m (line 80) | Likely OOM-kill/thrash: baseline measured up to ~196MB (caller). Use >=128m or accept swap. Also `mcuadros/ofelia:latest` (:643) is unpinned. |
| rspamd 300m | Tight on first start (rule/Hyperscan compilation and Lua maps; spike size UNVERIFIED). Suggest 384m; startup is when OOM would hit. |
| mysql 256m | OK for 32M pool + 60 conns; first-run init can spike (UNVERIFIED), 256m probably fine. |
| php-fpm 256m | Fine only with reduced pools: tracked pools allow 15+50 children each up to 256M/512M memory_limit (pools.conf), so 256m cap with default pools OOMs under load. Needs pools.slim.conf. |
| dovecot 256m | OK idle; doveadm/maildir_gc/quota recalcs and imapsync (ofelia jobs :313,:322) can spike. |
| dockerapi 96m / netfilter 96m / acme 96m | Plausible, UNVERIFIED. |
| mem_limit semantics | Without `memswap_limit`, Docker allows swap up to the same size again, so containers swap before OOM when host swap exists: caps are softer than the header says ("hard"). |
| Compose semantics | `command:` for mysql/memcached/php-fpm replaces CMD; mariadb entrypoint accepts leading `--` args (standard image behavior, UNVERIFIED here). No syntax errors found. Override does not remove any service, so header claim "stubs only sleep" is true but they still occupy containers. |
| Header misleading | (a) "olefy ... useless w/o clamd workflow": olefy is independent of clamd (olefy feeds rspamd oletools; clamd feeds antivirus; entrypoint :84-103). (b) "SKIP_FTS ... generate_config.sh writes n": compose default is y (:177) - generate_config claim UNVERIFIED by me. (c) The redis maxmemory/pools "cannot be set from override": they can via volume remount (see APPLY.md). (d) dockerapi/acme/ofelia cannot be removed freely (see section 1); header does not say so. (e) memcached/clamd/sogo limits are meaningless when stubs/absent. |

## 5. Deliverables
See docs/slim/conf/ and docs/slim/conf/APPLY.md.
