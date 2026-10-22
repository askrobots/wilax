#!/bin/sh
# SLIM: replacement for tracked data/conf/redis/redis-conf.sh (same content + maxmemory). Mount over it from the compose override:
#   redis-mailcow: volumes: - ./data/conf/redis/redis-slim.sh:/redis-conf.sh:z   (same target path replaces the base mount)
# Expected effect: bounds redis RSS at 96MB (+fork overhead). volatile-lru only evicts keys WITH a TTL; keys without TTL (e.g. rspamd history/bayes) are never evicted, so writes may error at the cap (acts like noeviction for them).
# RISK: if redis hits the cap with only non-TTL data, rspamd/PHP writes fail. Monitor `redis-cli info memory`. Not runtime-verified.
cat <<EOC > /redis.conf
requirepass $REDISPASS
user quota_notify on nopass ~QW_* -@all +get +hget +ping
maxmemory 96mb
maxmemory-policy volatile-lru
EOC

if [ -n "$REDISMASTERPASS" ]; then
  echo "masterauth $REDISMASTERPASS" >> /redis.conf
fi

exec redis-server /redis.conf
