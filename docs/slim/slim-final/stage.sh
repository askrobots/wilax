#!/bin/bash
# usage: stage.sh label settle_seconds
sleep ${2:-120}
cd /root
/root/measure.sh "$1"
python3 -I smoke.py --api-key "$(cat apikey)" --hostname mail.wilax-test.invalid 2>&1 | tail -14
H="Host: mail.wilax-test.invalid"; K="X-API-Key: $(cat apikey)"
for p in /api/v1/get/status/containers /api/v1/get/mailbox/all /api/v1/get/domain/all /api/v1/get/logs/rspamd-history/1 /api/v1/get/status/version; do
 curl -sk -o /dev/null -H "$H" -H "$K" -w "$p %{http_code} %{time_total}s\n" https://127.0.0.1$p; done
for p in /admin /debug /SOGo/; do curl -sk -o /dev/null -H "$H" -w "$p %{http_code}\n" https://127.0.0.1$p; done
