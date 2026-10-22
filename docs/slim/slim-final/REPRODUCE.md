# Reproduce the slim state (1GB / 1vCPU, Ubuntu, Docker + Compose >= 2.24)

```sh
# 0. host: 2GB swap + swappiness 10
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
sysctl -w vm.swappiness=10 && echo vm.swappiness=10 >> /etc/sysctl.d/99-slim.conf
# 1. stock tree + mailcow.conf (answers: hostname mail.mailwon-test.invalid, TZ)
cd /opt/mailwon && ./generate_config.sh
#    then apply the lines in mailcow.conf.changes (sed -i per key)
# 2. conf files (new files only; see slim-config.patch)
patch -p1 < docs/slim/slim-final/slim-config.patch
# 3. override
cp docs/slim/slim-final/docker-compose.slim.override.yml .
docker compose -f docker-compose.yml -f docker-compose.slim.override.yml config -q
docker compose -f docker-compose.yml -f docker-compose.slim.override.yml pull
docker compose -f docker-compose.yml -f docker-compose.slim.override.yml up -d --remove-orphans
# 4. wait ~3 min after all show Up/healthy. Create API key (test only; default admin creds are in mailcow docs):
source mailcow.conf; K=$(openssl rand -hex 16)
docker compose exec -T mysql-mailcow mysql -u$DBUSER -p$DBPASS $DBNAME -e \
 "insert into api (api_key,allow_from,skip_ip_check,access,active) values ('$K','127.0.0.1,172.22.1.0/24',1,'rw',1)"
# 5. smoke + measurement
python3 -I docs/slim/slim-final/smoke.py --api-key $K --hostname $MAILCOW_HOSTNAME
bash docs/slim/slim-final/measure.sh label   # measure.sh/stage.sh expect /root/apikey and /root/smoke.py
```
Notes: removed services use profile `removed`; if one was already running, `docker rm -f -v <container>` it once.
Patch note: `slim-config.patch` creates files only; `data/conf/rspamd/override.d/antivirus.conf` is required because clamd is removed.
