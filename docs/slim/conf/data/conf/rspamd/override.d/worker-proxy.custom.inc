# SLIM: included by tracked override.d/worker-proxy.inc (priority=30 .include try=true, line "worker-proxy.custom.inc").
# Pin the milter/proxy worker to one process. Expected: saves ~20-40MB per extra worker if rspamd would otherwise default to >1 (rspamd default for proxy count NOT verified in this repo).
# Risk: one proxy process serializes milter sessions on 1 vCPU, which is fine at this scale.
count = 1;
