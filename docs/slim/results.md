# wilax slim: measured results (droplet wilax-test, 1GB/1vCPU, 2GB swap, Compose v5.6.1)

All numbers measured 2026-10-22 on the droplet, localhost only. "Settled" = 1.5-3 min after the change; one-shot samples, not averages (noise ~ +/-30MB). `free -m` MiB. Per-container = cgroup memory.current + memory.swap.current. Repro: docs/slim/slim-final/.

| Stage | Config | used | swap used | avail | load | containers | top 5 (RAM+swap MiB) | smoke | API GET latency |
|---|---|---|---|---|---|---|---|---|---|
| S0 | baseline: SKIP_SOGO/CLAMD/OLEFY/FTS stubs (earlier report) | 727 | 686 | 234 | 0.12 | 18 | n/a (docker stats unreliable) | n/a | n/a |
| S1 | slim override (mem_limit, mariadb/php args), USE_WATCHDOG=n, stubs still run | 643 | 549 | 317 | 0.45 | 18 | rspamd 268, php-fpm 84, dockerapi 75, mysql 69, postfix 66 | 11/11 after smoke.py fixes | 0.25s |
| S2a | sogo, clamd, olefy truly removed (+rspamd antivirus off) | 765 | 427 | 195 | 0.15 | 15 | rspamd 233, php-fpm 115, ofelia 92, mysql 65, dockerapi 54 | 11/11 | 0.8s (cold) |
| S2b | + memcached removed | 669 | 585 | 292 | 0.10 | 14 | rspamd 230, ofelia 173, php-fpm 149, dockerapi 71, dovecot 65 | 11/11 | 0.27s |
| S2c | + watchdog removed | 666 | 631 | 294 | 0.06 | 13 | rspamd 228, ofelia 174, php-fpm 130, dovecot 88, dockerapi 68 | 11/11 | 0.28s |
| S2d | + netfilter removed | 679 | 599 | 281 | 0.05 | 12 | rspamd 234, ofelia 177, php-fpm 131, dovecot 75, dockerapi 69 | 11/11 | 0.19s |
| S2e | + postfix-tlspol removed (policy map line switched) | 700 | 548 | 261 | 0.09 | 11 | rspamd 234, ofelia 178, php-fpm 137, postfix 68, dockerapi 68 | 11/11 | 0.63s |
| S2f | acme removed (tested then reverted) | 647 | 546 | 314 | - | 10 | - | 11/11 | 0.17s |
| S3 | + php-fpm ondemand(6), redis maxmemory 96mb, mysql/dovecot/postfix/rspamd confs, swappiness 10 | 614 | 554 | 346 | 0.04 | 11 | rspamd 261, ofelia 173, dockerapi 75, mysql 67, dovecot 61 | 11/11 | 0.52s |
| S4 | final override file (acme kept as stub), 3 min settled | 659-669 | 507 | 292-301 | 0.06 | 11 | rspamd 258, ofelia 151, dockerapi 69, php-fpm 66, mysql 59 | 11/11 x4 runs | 0.14s |

vmstat: idle si/so = 0; a smoke run or API burst causes one-second spikes of ~17-30MB/s si and so (swap thrash visible as 24-35% iowait for 1 s). No OOM kills of functional services after the final config; 2 early ofelia OOM kills were at the 32m limit from the draft override (fixed: 96m).

## What worked
- Truly removing 8 services (sogo, clamd, olefy, memcached, watchdog, netfilter, postfix-tlspol; acme optional) via `profiles: ["removed"]` + `depends_on: !override`. 18 -> 11 containers. Smoke 11/11 and admin API/UI (domains, mailboxes, status/containers, rspamd history) answer at every step; /SOGo/ returns 404 as expected. php-fpm logs only "Cleaned up memcached" on init, no errors.
- smoke.py was run for real: fixed the DKIM step (mailcow auto-creates a key on domain add so `add/dkim` returned dkim_domain_or_sel_exists; now checks existing key first). Submission mail carries no X-Spamd-Result (authenticated mail skips it), so the spam-header check needs the port-25 message; both run fine from 127.0.0.1.
- API key via `insert into api(...)` in the mysql container works (allow_from 127.0.0.1 + docker net, skip_ip_check=1).
- Override config validated (`config -q`); compose `!override` needs >= 2.24 (v5.6.1 ok).

## What failed / caveats
- Removing services moved RAM total very little: "used" stays 610-770MB. The savings were mostly stubs that were already swapped out (stub RSS is a few MB). The big consumers are unchanged: rspamd (~260MB incl. swap), ofelia (~95MB RAM, a Go binary holding a docker.sock event loop; oddly large), dockerapi (~70MB), php-fpm, mysql, dovecot, postfix, unbound.
- ofelia needs >= 96m or it is OOM-killed (the draft's 32m killed it twice).
- Swap used stays ~500-550MB at idle: the box fits only because of swap. Bursts cause 1-second iowait spikes; API latency ranges 0.14s (warm) to 0.8s (swapped-out). It works, but any real load (several IMAP clients, spam scanning) would thrash.
- acme removal works only because certs already exist in data/assets/ssl; on a fresh install acme creates the self-signed cert, so keep it (final keeps the stub).
- tlspol removal drops MTA-STS/DANE for outbound; outbound delivery was NOT tested (no DNS). Netfilter/watchdog removal drops brute-force banning and auto-heal; mail ports are firewalled on the droplet only by the DOCKER-USER rule.
- S3 per-knob effect is not separable from noise in one-shot samples (used 614 vs 700 at S2e). php-fpm RAM fell (137 -> 41-72MB) which is the clearest gain. Dovecot/mariadb/redis caps made no measurable difference at idle.
- Not tested: real load, ClamAV/SOGo re-enable, outbound mail, TLS from remote clients, restart-from-cold timing of the slim stack.

## Conclusion
A headless 11-container mailcow (no webmail, AV, FTS, bans, watchdog) passes the smoke test on 1GB/1vCPU with 2GB swap, idle at ~650MB used + ~510MB swap with ~300MB available. That is "it runs", not "it is comfortable". The remaining 11 containers are ~40MB docker/OS overhead plus ~8 Go/Python/C daemons each with fixed footprints; further config trimming is unlikely to give >50-80MB.

## Next replacements (by expected saving)
1. rspamd (~260MB): cut the module set hard (asn, phishing, spamassassin, reputation, mx_check, fuzzy), or replace with a milter-only DKIM/SPF/DMARC stack. Measure per-module.
2. dockerapi + ofelia + php-fpm (~230MB combined): replace ofelia with host cron and make dockerapi optional/stub in data/web/inc/prerequisites.inc.php (currently HTTP 500 without it, per FINDINGS).
3. mariadb (~65MB) -> SQLite is a large code change; lower priority. Alternatively a 2GB droplet removes the swap dependence for $5 more.
