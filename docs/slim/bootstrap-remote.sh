#!/usr/bin/env bash
# Runs as root ON the fresh Ubuntu 24.04 droplet (idempotent). Tree is at /opt/mailwon.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/mailwon
cd "$APP"

echo "== packages"
apt-get update -y
apt-get install -y git rsync curl ca-certificates openssl python3 iptables

echo "== docker"
# Official convenience script (get.docker.com): gives current engine + compose plugin
# in one step, which generate_config.sh requires (Docker >= 24); Ubuntu's docker.io lags.
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "== swap 2GB, swappiness 10"
if ! swapon --show=NAME --noheadings | grep -q .; then
  [ -f /swapfile ] || { fallocate -l 2G /swapfile; chmod 600 /swapfile; mkswap /swapfile; }
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/99-mailwon.conf
sysctl -q -p /etc/sysctl.d/99-mailwon.conf

echo "== firewall (only SSH from the internet, including Docker-published ports)"
# ufw alone does NOT stop Docker-published ports: Docker inserts its own DNAT/FORWARD
# rules that bypass ufw's INPUT chain. The DOCKER-USER chain is evaluated first for
# forwarded container traffic, so we drop there. Return traffic of outbound connections
# (e.g. DNS, image pulls from containers) must be allowed via conntrack.
cat > /usr/local/sbin/mailwon-fw.sh <<'FW'
#!/usr/bin/env bash
IF="$(ip -4 route show default | awk '{print $5; exit}')"
iptables -N DOCKER-USER 2>/dev/null || true
iptables -F DOCKER-USER
iptables -A DOCKER-USER -i "$IF" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A DOCKER-USER -i "$IF" -j DROP
iptables -A DOCKER-USER -j RETURN
FW
chmod +x /usr/local/sbin/mailwon-fw.sh
cat > /etc/systemd/system/mailwon-fw.service <<'UNIT'
[Unit]
Description=mailwon: drop public traffic to Docker-published ports
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/mailwon-fw.sh
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mailwon-fw.service
systemctl restart mailwon-fw.service

echo "== mailcow.conf"
ln -sf mailcow.conf .env
if [ ! -f mailcow.conf ]; then
  # generate_config.sh --dev needs a git repo with an upstream (rsync excludes .git):
  # make a throwaway local one backed by a local bare "origin".
  if [ ! -d .git ]; then
    git init -q -b main . 
    git -c user.email=a@b -c user.name=mailwon add -A >/dev/null 2>&1 || true
    git -c user.email=a@b -c user.name=mailwon commit -q -m bootstrap --allow-empty
    rm -rf /var/tmp/mailwon-origin.git; git init -q --bare /var/tmp/mailwon-origin.git
    git remote add origin /var/tmp/mailwon-origin.git
    git push -q -u origin main
  fi
  # Env vars accepted by generate_config.sh: MAILCOW_HOSTNAME, MAILCOW_TZ, SKIP_CLAMD
  # (MAILCOW_DBPASS/DBROOT/REDISPASS default to random). Other SKIP_* are set below.
  MAILCOW_HOSTNAME=mail.mailwon-test.invalid MAILCOW_TZ=UTC SKIP_CLAMD=y \
    ./generate_config.sh --dev </dev/null
fi
setconf() { # key value
  if grep -q "^$1=" mailcow.conf; then sed -i "s|^$1=.*|$1=$2|" mailcow.conf; else echo "$1=$2" >> mailcow.conf; fi
}
setconf MAILCOW_HOSTNAME mail.mailwon-test.invalid
setconf TZ UTC
setconf SKIP_LETS_ENCRYPT y
setconf SKIP_CLAMD y
setconf SKIP_OLEFY y
setconf SKIP_SOGO y
setconf SKIP_FTS y
setconf HTTP_BIND 127.0.0.1
setconf HTTPS_BIND 127.0.0.1
setconf ENABLE_IPV6 false
# The next three were proven on the test droplet (docs/slim/results.md, slim-final/mailcow.conf.changes)
setconf USE_WATCHDOG n
setconf SQL_PORT 127.0.0.1:13306
setconf REDIS_PORT 127.0.0.1:7654
chmod 600 mailcow.conf

echo "== slim config files (new files only; rspamd antivirus.conf is required because clamd is removed)"
patch -p1 -N --forward < docs/slim/slim-final/slim-config.patch || echo "patch: already applied, or failed: check data/conf/rspamd/override.d/antivirus.conf exists"

echo "== start stack"
DC="docker compose -f docker-compose.yml -f docker-compose.slim.override.yml"
$DC pull
$DC up -d --remove-orphans

echo "== wait for healthy (timeout 15 min)"
deadline=$(( $(date +%s) + 900 ))
while :; do
  bad="$($DC ps --format '{{.Name}} {{.Status}}' | grep -Ei 'starting|unhealthy|restarting|exited' || true)"
  [ -z "$bad" ] && break
  [ "$(date +%s)" -lt "$deadline" ] || { echo "TIMEOUT, not healthy:"; echo "$bad"; free -m; exit 1; }
  sleep 10
done

echo "== API key for smoke test (best effort, stored at /root/.mailwon-api-key)"
if [ ! -s /root/.mailwon-api-key ]; then
  KEY="$(LC_ALL=C tr -dc 'A-F0-9' </dev/urandom | head -c 30 | sed 's/\(......\)/\1-/g;s/-$//')"
  if $DC exec -T mysql-mailcow sh -c 'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" mailcow' >/dev/null 2>&1 <<SQL
INSERT INTO api (api_key, allow_from, skip_ip_check, access, active) VALUES ('$KEY', '127.0.0.1,172.22.1.0/24', 1, 'rw', 1);
SQL
  then ( umask 077; echo "$KEY" > /root/.mailwon-api-key ); echo "API key written."
  else echo "WARN: could not insert API key; create one in the admin UI."; fi
fi

echo "== memory"
free -m
echo "bootstrap done"
