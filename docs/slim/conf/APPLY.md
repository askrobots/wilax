# Applying docs/slim/conf (run from repo root on the droplet; nothing here edits a tracked upstream file)

All paths below are relative to the repo root. Nothing was run; every file is unverified at runtime.

| Step | Command | Restart | Risk |
|---|---|---|---|
| 1 mysql | `cp docs/slim/conf/data/conf/mysql/slim.cnf data/conf/mysql/` | mysql-mailcow | Low. New file in the dir mounted at /etc/mysql/conf.d (docker-compose.yml:28). |
| 2 dovecot | `cp docs/slim/conf/data/conf/dovecot/extra.conf data/conf/dovecot/` (if extra.conf exists already, append instead) | dovecot-mailcow | Low/med. Gitignored (.gitignore:15). Check `docker compose exec dovecot-mailcow doveconf -n \| grep -E 'process_min_avail\|process_limit'`. |
| 3 postfix | `cp docs/slim/conf/data/conf/postfix/extra.cf data/conf/postfix/` (append if exists) | postfix-mailcow | Low. Gitignored (.gitignore:37). The tlspol line stays commented unless tlspol is removed. |
| 4 rspamd | `cp docs/slim/conf/data/conf/rspamd/override.d/{neural.conf,worker-proxy.custom.inc} data/conf/rspamd/override.d/` and ONLY if clamd is absent also `antivirus.conf` | rspamd-mailcow | Low. Check `docker compose exec rspamd-mailcow rspamadm configdump -m antivirus` afterwards. |
| 5 php-fpm pools | `cp docs/slim/conf/data/conf/phpfpm/php-fpm.d/pools.slim.conf data/conf/phpfpm/php-fpm.d/` then add to the compose override (agent A's file): `php-fpm-mailcow: volumes: ["./data/conf/phpfpm/php-fpm.d/pools.slim.conf:/usr/local/etc/php-fpm.d/z-pools.conf:Z"]` (same target path replaces the base mount; list volumes merge by target) | php-fpm-mailcow | Med. Queueing under >6 concurrent requests. Regenerate after upstream pools.conf changes: `awk '/^pm = dynamic/{print "pm = ondemand";next} /^pm.max_children/{print "pm.max_children = 6";print "pm.process_idle_timeout = 15s";next} /^pm.(start_servers|min_spare_servers|max_spare_servers)/{next} {print}' data/conf/phpfpm/php-fpm.d/pools.conf > data/conf/phpfpm/php-fpm.d/pools.slim.conf`. |
| 6 redis | `cp docs/slim/conf/data/conf/redis/redis-slim.sh data/conf/redis/` then in override: `redis-mailcow: volumes: ["./data/conf/redis/redis-slim.sh:/redis-conf.sh:z"]` | redis-mailcow | Med. Writes fail at 96MB if only non-TTL data. Alternative with no file: `docker compose exec redis-mailcow redis-cli -a $REDISPASS config set maxmemory 96mb` (lost on restart). |

Git note: .gitignore lines 44-45 ignore data/conf/rspamd/local.d/* and override.d/*, so new files there are not tracked unless `git add -f`; that avoids upstream conflicts but they will not travel with the fork. The copies in docs/slim/conf are the tracked source of truth.

Apply order: 1-4 can be done before first `up`; 5-6 need the override yml change. Then `docker compose up -d` and watch `docker stats --no-stream`.
