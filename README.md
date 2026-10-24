# Wilax

**Wilax is a fork of [mailcow: dockerized](https://github.com/mailcow/mailcow-dockerized) (GPL-3.0) being slimmed down to run on a 1 GB RAM / 1 vCPU server (a $7/month droplet).** It is an independent fork, not affiliated with or endorsed by the mailcow project. Everything below this section is the upstream README, unchanged.

## The name

Wilax was called mailwon until 2026-10-24. The new name credits where it came from: add up the letters of "mailcow" as you go (a=1 ... z=26, wrapping past z) and you get m, n, **w, i, l, a, x**. With thanks to the mailcow team, whose work this is built on.

## Why

[askrobots.com](https://askrobots.com) hosts email for its users and uses Mailcow only as the mail engine: its API creates domains, mailboxes and aliases and reads DKIM keys, logs and the quarantine, and the app reads mail over IMAP into its own database. Users never see SOGo (webmail) or the Mailcow admin UI. Stock Mailcow wants 6 GB of RAM and ~18 containers; on a 1 GB host it only runs by swapping heavily (measured: ~750 MB RAM plus ~980 MB swap with the `SKIP_*` flags set, because those flags leave idle stub containers running). That is slow, and most of what runs is not used. The goal is a headless mail engine that fits a small server.

## What was done (2026-10-22)

All numbers were measured on a real 1 GB / 1 vCPU DigitalOcean droplet with 2 GB swap, localhost only. Details: [`docs/slim/results.md`](docs/slim/results.md).

- **Seven services really removed**, not left as stubs: SOGo, ClamAV, olefy, memcached, watchdog, netfilter, postfix-tlspol. Done in [`docker-compose.slim.override.yml`](docker-compose.slim.override.yml) with compose `profiles` plus `depends_on: !override` (needs Docker Compose >= 2.24). 18 containers became 11. `dockerapi` and `unbound` must stay: the PHP layer returns HTTP 500 without dockerapi, and unbound is every service's DNS. `ofelia` stays (it runs the cron jobs) and needs `mem_limit` of at least 96m or it is OOM-killed.
- **Tuning** as new files only, so upstream merges stay clean: php-fpm on-demand pool, redis and MariaDB limits, Dovecot/Postfix/Rspamd settings, Rspamd antivirus off ([`docs/slim/conf/`](docs/slim/conf/), applied by [`slim-final/slim-config.patch`](docs/slim/slim-final/slim-config.patch)).
- **Result:** the 11-container stack passes the smoke test 11/11, idle at roughly 660-720 MB RAM plus 480-530 MB swap, load ~0.05. **It fits only because of swap**; bursts cause one-second swap spikes and API latency of 0.14 s warm to ~0.8 s when swapped out.
- **Tests** (upstream had almost none): [`tests/test_static.py`](tests/test_static.py) checks compose and override integrity and script syntax (no Docker needed; `python3 -I -m unittest discover -s tests`, run in CI by `.github/workflows/wilax_tests.yml`), and [`helper-scripts/dev_tests/smoke.py`](helper-scripts/dev_tests/smoke.py) runs against a live stack: add domain and mailbox over the API, DKIM, SMTP submission and port 25, IMAP delivery, DKIM-Signature and spam headers, cleanup.
- **Disposable test environment:** [`docs/slim/provision-droplet.sh`](docs/slim/provision-droplet.sh) creates, smoke-tests and destroys a tagged test droplet. It was validated end to end, and doing so found and fixed four bugs.

## What is NOT done or not verified

- Outbound mail was not tested (no DNS), so removing postfix-tlspol (which drops MTA-STS/DANE) is unverified.
- Removing netfilter and watchdog drops brute-force banning and auto-heal. That is acceptable for a private box, not for a public mail server.
- Not tested: real load, cold-start time, re-enabling SOGo or ClamAV, TLS from remote clients.
- The remaining memory is Rspamd (~260 MB), ofelia, dockerapi, MariaDB and php-fpm. Getting well under 1 GB without swap means trimming Rspamd and replacing ofelia and dockerapi, or moving to a 2 GB droplet.

## Next

Trim Rspamd module by module; replace ofelia with host cron and make dockerapi optional; test outbound mail; then a thin replacement client with the same ~20 methods the askrobots app uses, and direct delivery to the app instead of IMAP polling. Start at [`docs/slim/HANDOFF.md`](docs/slim/HANDOFF.md) and [`docs/slim/results.md`](docs/slim/results.md).

## License

GPL-3.0, as upstream ([`LICENSE`](LICENSE)). Changes in this fork are published under the same license. The mailcow name, logo and branding belong to their owners; this fork is renamed for that reason.

---

# mailcow: dockerized - 🐮 + 🐋 = 💕

[![Translation status](https://translate.mailcow.email/widgets/mailcow-dockerized/-/translation/svg-badge.svg)](https://translate.mailcow.email/engage/mailcow-dockerized/)
[![Twitter URL](https://img.shields.io/twitter/url/https/twitter.com/mailcow_email.svg?style=social&label=Follow%20%40mailcow_email)](https://twitter.com/mailcow_email)
![Mastodon Follow](https://img.shields.io/mastodon/follow/109388212176073348?domain=https%3A%2F%2Fmailcow.social&label=Follow%20%40doncow%40mailcow.social&link=https%3A%2F%2Fmailcow.social%2F%40doncow)


## Want to support mailcow?

Please [consider a support contract with Servercow](https://www.servercow.de/mailcow?lang=en#support) to support further development. _We_ support _you_ while _you_ support _us_. :)

You can also [get a SAL](https://www.servercow.de/mailcow?lang=en#sal) which is a one-time payment with no liabilities or returning fees.

Or just spread the word: moo.

## Many thanks to our GitHub Sponsors ❤️
A big thank you to everyone supporting us on GitHub Sponsors—your contributions mean the world to us! Special thanks to the following amazing supporters:

### 100$/Month Sponsors
  <a href="https://www.colba.net/" target=_blank><img
    src="https://avatars.githubusercontent.com/u/204464723" height="58"
  /></a>
  <a href="https://www.maehdros.com/" target=_blank><img
    src="https://avatars.githubusercontent.com/u/173894712" height="58"
  /></a>
  <a href="https://garske-systems.de/" target=_blank><img
    src="https://garske-systems.de/img/logo_min.png" height="58"
  /></a>

### 50$/Month Sponsors
  <a href="https://github.com/vnukhr" target=_blank><img
    src="https://avatars.githubusercontent.com/u/7805987?s=52&v=4" height="58"
  /></a><a href="https://github.com/humiico" target=_blank><img
    src="https://avatars.githubusercontent.com/u/110134705?s=52&v=4" height="58"
  /></a>

## Info, documentation and support

Please see [the official documentation](https://docs.mailcow.email/) for installation and support instructions. 🐄

🐛 **If you found a critical security issue, please mail us to [info at servercow.de](mailto:info@servercow.de).**

## Cowmunity

[mailcow community](https://community.mailcow.email)

[Telegram mailcow channel](https://telegram.me/mailcow)

[Telegram mailcow Off-Topic channel](https://t.me/mailcowOfftopic)

[Official 𝕏 (Twitter) Account](https://twitter.com/mailcow_email)

[Official Mastodon Account](https://mailcow.social/@doncow)

Telegram desktop clients are available for [multiple platforms](https://desktop.telegram.org). You can search the groups history for keywords.

## Misc

**Important**: mailcow makes use of various open-source software. Please assure you agree with their license before using mailcow.
Any part of mailcow itself is released under **GNU General Public License, Version 3**.

mailcow is a registered word mark of The Infrastructure Company GmbH, Parkstr. 42, 47877 Willich, Germany.

The project is managed and maintained by The Infrastructure Company GmbH.

Originated from @andryyy (André)
