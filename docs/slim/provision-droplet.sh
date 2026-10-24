#!/usr/bin/env bash
# Recreate / manage the disposable wilax test droplet from one script.
# Runs on the user's Mac. Needs: doctl (authenticated), ssh, rsync.
#
# SAFETY: this script only ever creates or deletes droplets carrying the tag
# "wilax-test". It NEVER touches the droplets named `ubuntu-s-1vcpu-1gb-amd-nyc1`
# or `nc`, nor the production Mailcow, nor any droplet without that tag.
#
# Usage: provision-droplet.sh create [name] | status | ssh | smoke | destroy [--yes]
set -euo pipefail

TAG="wilax-test"
DEFAULT_NAME="wilax-test2"
REGION="nyc1"
SIZE="s-1vcpu-1gb-amd"
IMAGE="ubuntu-24-04-x64"
SSH_KEY_ID="38944259"
HOURLY="0.0104"   # USD/hr for $7/mo plan (doctl size list: 0.010420)
PROTECTED_NAMES="ubuntu-s-1vcpu-1gb-amd-nyc1 nc"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$HOME/.ssh/known_hosts.wilax -o ConnectTimeout=8 -o LogLevel=ERROR"

die() { echo "ERROR: $*" >&2; exit 1; }
command -v doctl >/dev/null || die "doctl not found"

# Prints "id name ip" for droplets with our tag.
tagged() { doctl compute droplet list --tag-name "$TAG" --no-header --format ID,Name,PublicIPv4; }

# First tagged droplet IP (optionally by name).
tagged_ip() {
  local name="${1:-}" line
  line="$(tagged | awk -v n="$name" 'n=="" || $2==n {print $3; exit}')"
  [ -n "$line" ] || die "no droplet tagged $TAG${name:+ named $name} (run: $0 create)"
  echo "$line"
}

is_protected() { local n; for n in $PROTECTED_NAMES; do [ "$n" = "$1" ] && return 0; done; return 1; }

cmd_create() {
  local name="${1:-$DEFAULT_NAME}"
  is_protected "$name" && die "refusing: $name is a protected droplet name"
  # Refuse if ANY droplet already has this name (we would not have created it).
  if doctl compute droplet list --no-header --format Name | grep -Fxq "$name"; then
    die "a droplet named $name already exists; refusing (use status / destroy, or pick another name)"
  fi
  echo "Creating $name ($SIZE, $REGION, $IMAGE, tag $TAG)..."
  doctl compute droplet create "$name" --region "$REGION" --size "$SIZE" --image "$IMAGE" \
    --ssh-keys "$SSH_KEY_ID" --tag-name "$TAG" --wait >/dev/null
  local ip
  ip="$(tagged | awk -v n="$name" '$2==n {print $3; exit}')"
  [ -n "$ip" ] || die "created but could not find IP; check: doctl compute droplet list"
  echo "IP: $ip. Waiting for SSH..."
  local i=0
  until ssh $SSH_OPTS -o BatchMode=yes "root@$ip" true 2>/dev/null; do
    i=$((i+1)); [ $i -le 60 ] || die "SSH not up after ~5 min (droplet still exists and bills: $0 destroy)"
    sleep 5
  done
  sync_tree "$ip"
  echo "Running bootstrap-remote.sh (several minutes: apt, docker, image pull)..."
  ssh $SSH_OPTS "root@$ip" "bash /opt/wilax/docs/slim/bootstrap-remote.sh"
  echo
  echo "Droplet $name ready at $ip"
  echo "Cost: about \$$HOURLY/hour (\$7/month). DESTROY IT WHEN DONE: $0 destroy"
}

sync_tree() {
  local ip="$1"
  ssh $SSH_OPTS "root@$ip" "mkdir -p /opt/wilax"
  rsync -az --delete -e "ssh $SSH_OPTS" \
    --exclude .git --exclude venv --exclude .venv --exclude node_modules \
    --exclude mailcow.conf --exclude .env \
    "$REPO"/ "root@$ip:/opt/wilax/"
}

cmd_status() {
  local out; out="$(tagged)"
  if [ -z "$out" ]; then echo "No droplets tagged $TAG."; return 0; fi
  echo "$out"
  local ip; ip="$(echo "$out" | awk 'NR==1{print $3}')"
  ssh $SSH_OPTS "root@$ip" "free -m; docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -30" || true
  echo "Billing about \$$HOURLY/hour per droplet while it exists."
}

cmd_ssh() { local ip; ip="$(tagged_ip "${1:-}")"; exec ssh $SSH_OPTS "root@$ip"; }

cmd_smoke() {
  local ip; ip="$(tagged_ip "${1:-}")"
  # Copy ONLY the test script. Never re-run sync_tree here: its rsync --delete removes files the bootstrap
  # created on the droplet (data/assets/ssl, the slim data/conf files), which broke Postfix TLS.
  scp $SSH_OPTS "$(dirname "$0")/../../helper-scripts/dev_tests/smoke.py" "root@$ip:/opt/wilax/helper-scripts/dev_tests/smoke.py"
  # API key was created by bootstrap in /root/.wilax-api-key (never printed here).
  ssh $SSH_OPTS "root@$ip" 'cd /opt/wilax && [ -s /root/.wilax-api-key ] || { echo "no API key at /root/.wilax-api-key (bootstrap could not create one; create in admin UI)"; exit 1; }
    MAILCOW_API_KEY="$(cat /root/.wilax-api-key)" MAILCOW_URL=https://127.0.0.1 MAILCOW_HOSTNAME=mail.wilax-test.invalid \
    SMOKE_SKIP="${SMOKE_SKIP:-}" python3 -I helper-scripts/dev_tests/smoke.py'
}

cmd_destroy() {
  local yes=0; [ "${1:-}" = "--yes" ] && yes=1
  local out; out="$(tagged)"
  if [ -z "$out" ]; then
    echo "No droplets tagged $TAG; nothing to delete."
  else
    echo "Droplets tagged $TAG that will be DELETED:"; echo "$out"
    # Defence in depth: abort if a protected name ever shows up.
    local n; for n in $PROTECTED_NAMES; do
      echo "$out" | awk '{print $2}' | grep -Fxq "$n" && die "protected droplet $n carries the tag; aborting"
    done
    if [ $yes -ne 1 ]; then
      printf 'Type "destroy" to confirm: '; read -r ans
      [ "$ans" = "destroy" ] || die "not confirmed"
    fi
    echo "$out" | awk '{print $1}' | while read -r id; do
      doctl compute droplet delete "$id" --force
    done
  fi
  echo; echo "Remaining droplets (verify nothing else was touched):"
  doctl compute droplet list --format ID,Name,PublicIPv4,Tags
}

case "${1:-}" in
  create)  shift; cmd_create "$@" ;;
  status)  cmd_status ;;
  ssh)     shift; cmd_ssh "$@" ;;
  smoke)   shift; cmd_smoke "$@" ;;
  destroy) shift; cmd_destroy "$@" ;;
  *) echo "usage: $0 create [name] | status | ssh [name] | smoke [name] | destroy [--yes]"; exit 2 ;;
esac
