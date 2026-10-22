#!/bin/bash
# usage: measure.sh label
echo "=== $1 $(date -u +%H:%M:%S)"
free -m | sed -n 2,3p; uptime
vmstat 1 5 | tail -3
echo "-- top containers (RAM+swap MiB)"
for id in $(docker ps -q --no-trunc); do
 n=$(docker inspect -f '{{.Name}}' $id | sed 's/\/mailcowdockerized-//;s/-1$//')
 d=/sys/fs/cgroup/system.slice/docker-$id.scope
 m=$(cat $d/memory.current 2>/dev/null||echo 0); s=$(cat $d/memory.swap.current 2>/dev/null||echo 0)
 echo "$(( (m+s)/1048576 )) ram=$((m/1048576)) swap=$((s/1048576)) $n"
done | sort -rn | head -${TOPN:-8}
echo "containers: $(docker ps -q|wc -l)  oom: $(dmesg | grep -ci 'out of memory')"
KEY=$(cat /root/apikey 2>/dev/null)
curl -sk -o /dev/null -H "Host: mail.mailwon-test.invalid" -H "X-API-Key: $KEY" -w "api GET %{http_code} %{time_total}s\n" https://127.0.0.1/api/v1/get/status/containers
curl -sk -o /dev/null -H "Host: mail.mailwon-test.invalid" -w "login page %{http_code} %{time_total}s\n" https://127.0.0.1/
