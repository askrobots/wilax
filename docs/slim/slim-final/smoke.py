#!/usr/bin/env python3
"""mailwon smoke test. Run: python3 -I smoke.py

Creates a temp domain+mailbox via the API, sends mail over submission (587,
authenticated, STARTTLS) and over port 25 (unauthenticated), fetches via IMAPS,
checks DKIM-Signature and X-Spamd-Result headers, then cleans up.
Localhost only by default; no outbound mail, no real DNS needed.

Env (or flags of the same name, lowercase, e.g. --api-key):
  MAILCOW_API_KEY    required, read/write API key (X-API-Key)
  MAILCOW_URL        default https://127.0.0.1  (self-signed accepted)
  MAILCOW_HOSTNAME   Host header for the API, default: none (use if nginx
                     routes by server_name; e.g. mail.example.org)
  SMOKE_DOMAIN       default smoke-<rand>.test
  SMOKE_PASSWORD     default random
  SMOKE_HOST         SMTP/IMAP host, default 127.0.0.1
  SMOKE_SUBMISSION_PORT 587, SMOKE_SMTP_PORT 25, SMOKE_IMAPS_PORT 993
  SMOKE_SKIP         comma list of disabled components: dkim,rspamd,smtp25
                     (their checks are reported SKIP instead of FAIL)
  SMOKE_KEEP         1 = do not clean up
"""
import argparse, email, imaplib, json, os, secrets, smtplib, ssl, sys, time
import urllib.error, urllib.request

ROWS = []  # (step, status, seconds, detail)


def record(step, status, t0, detail=""):
    ROWS.append((step, status, time.monotonic() - t0, detail))


class Skip(Exception):
    pass


def step(name):
    """Decorator-ish runner: fn returns detail str; Skip -> SKIP; other exc -> FAIL."""
    def run(fn):
        t0 = time.monotonic()
        try:
            d = fn()
            record(name, "PASS", t0, d or "")
            return True
        except Skip as e:
            record(name, "SKIP", t0, str(e))
        except Exception as e:  # noqa
            record(name, "FAIL", t0, f"{type(e).__name__}: {e}"[:200])
        return False
    return run


def ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class Api:
    def __init__(self, base, key, hostname):
        self.base, self.key, self.hostname = base.rstrip("/"), key, hostname

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-API-Key", self.key)
        req.add_header("Content-Type", "application/json")
        if self.hostname:
            req.add_header("Host", self.hostname)
        try:
            with urllib.request.urlopen(req, context=ctx(), timeout=60) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} {path}: {e.read().decode()[:150]}")
        try:
            return json.loads(raw)
        except ValueError:
            raise RuntimeError(f"non-JSON reply from {path}: {raw[:100]!r}")

    def post_ok(self, path, body):
        """mailcow returns a list of {type, msg}; any type != success is an error."""
        res = self.call("POST", path, body)
        items = res if isinstance(res, list) else [res]
        for it in items:
            if not isinstance(it, dict) or it.get("type") != "success":
                raise RuntimeError(f"{path}: {json.dumps(it)[:150]}")
        return items


def build_msg(frm, to, marker):
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, f"smoke {marker}"
    m["Message-ID"] = f"<{marker}@smoke.invalid>"
    m["Date"] = email.utils.formatdate(localtime=False)
    m.set_content(f"mailwon smoke test {marker}\n")
    return m


def fetch(host, port, user, pw, marker, timeout=60):
    """Poll INBOX (then Junk) for message with marker in Subject; return parsed msg."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        M = imaplib.IMAP4_SSL(host, port, ssl_context=ctx(), timeout=30)
        try:
            M.login(user, pw)
            for box in ("INBOX", "Junk"):
                if M.select(box, readonly=True)[0] != "OK":
                    continue
                typ, d = M.search(None, "SUBJECT", f'"{marker}"')
                if typ == "OK" and d[0].split():
                    typ, md = M.fetch(d[0].split()[-1], "(RFC822)")
                    return email.message_from_bytes(md[0][1])
        finally:
            try:
                M.logout()
            except Exception:
                pass
        time.sleep(2)
    raise RuntimeError(f"message {marker} not delivered within {timeout}s")


def main():
    ap = argparse.ArgumentParser()
    E = os.environ.get
    ap.add_argument("--url", default=E("MAILCOW_URL", "https://127.0.0.1"))
    ap.add_argument("--api-key", default=E("MAILCOW_API_KEY"))
    ap.add_argument("--hostname", default=E("MAILCOW_HOSTNAME"))
    ap.add_argument("--domain", default=E("SMOKE_DOMAIN", f"smoke-{secrets.token_hex(3)}.test"))
    ap.add_argument("--password", default=E("SMOKE_PASSWORD") or secrets.token_urlsafe(16) + "aA1")
    ap.add_argument("--host", default=E("SMOKE_HOST", "127.0.0.1"))
    ap.add_argument("--submission-port", type=int, default=int(E("SMOKE_SUBMISSION_PORT", 587)))
    ap.add_argument("--smtp-port", type=int, default=int(E("SMOKE_SMTP_PORT", 25)))
    ap.add_argument("--imaps-port", type=int, default=int(E("SMOKE_IMAPS_PORT", 993)))
    ap.add_argument("--skip", default=E("SMOKE_SKIP", ""))
    ap.add_argument("--keep", action="store_true", default=E("SMOKE_KEEP") == "1")
    a = ap.parse_args()
    if not a.api_key:
        print("MAILCOW_API_KEY (or --api-key) is required", file=sys.stderr)
        return 2
    skip = {s.strip().lower() for s in a.skip.split(",") if s.strip()}
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"note: SMOKE_HOST={a.host} is not localhost", file=sys.stderr)

    api = Api(a.url, a.api_key, a.hostname)
    user = f"smoke@{a.domain}"
    state = {}
    created_domain = created_mbox = False

    def s_domain():
        api.post_ok("/api/v1/add/domain", {
            "domain": a.domain, "description": "smoke test", "active": "1",
            "quota": "1024", "defquota": "256", "maxquota": "512",
            "mailboxes": "5", "aliases": "10", "relay_all_recipients": "0",
            "restart_sogo": "0"})
        return a.domain
    created_domain = step("api: add domain")(s_domain)

    def s_mbox():
        api.post_ok("/api/v1/add/mailbox", {
            "local_part": "smoke", "domain": a.domain, "name": "Smoke",
            "password": a.password, "password2": a.password, "quota": "256",
            "active": "1", "force_pw_update": "0", "tls_enforce_in": "0",
            "tls_enforce_out": "0"})
        return user
    if created_domain:
        created_mbox = step("api: add mailbox")(s_mbox)

    def s_dkim():
        if "dkim" in skip:
            raise Skip("dkim disabled (SMOKE_SKIP)")
        # mailcow auto-creates a key when a domain is added; only add one if missing
        cur = api.call("GET", f"/api/v1/get/dkim/{a.domain}")
        if isinstance(cur, dict) and cur.get("pubkey"):
            return "key present (auto-created)"
        api.post_ok("/api/v1/add/dkim", {
            "domains": a.domain, "dkim_selector": "dkim", "key_size": "2048"})
        return "key generated"
    if created_domain:
        step("api: dkim key")(s_dkim)

    m_sub, m_25 = secrets.token_hex(6), secrets.token_hex(6)

    def s_submit():
        with smtplib.SMTP(a.host, a.submission_port, timeout=30) as s:
            s.ehlo("smoke.invalid")
            s.starttls(context=ctx())
            s.ehlo("smoke.invalid")
            s.login(user, a.password)
            s.send_message(build_msg(user, user, m_sub))
        return f"port {a.submission_port}"
    sent_sub = created_mbox and step("smtp: submission (auth)")(s_submit)

    def s_smtp25():
        if "smtp25" in skip:
            raise Skip("port 25 disabled (SMOKE_SKIP)")
        with smtplib.SMTP(a.host, a.smtp_port, timeout=30) as s:
            s.ehlo("smoke.invalid")
            try:
                s.starttls(context=ctx())
                s.ehlo("smoke.invalid")
            except smtplib.SMTPNotSupportedError:
                pass
            # sender is a non-local, non-resolvable name; only local rcpt is used
            s.send_message(build_msg("probe@smoke-sender.invalid", user, m_25))
        return f"port {a.smtp_port}"
    sent_25 = created_mbox and step("smtp: port 25 (unauth)")(s_smtp25)

    def getter(label, marker):
        def f():
            state[label] = fetch(a.host, a.imaps_port, user, a.password, marker)
            return "delivered"
        return f
    if sent_sub:
        step("imap: fetch submission msg")(getter("sub", m_sub))
    if sent_25:
        step("imap: fetch port-25 msg")(getter("p25", m_25))

    def s_dkim_hdr():
        if "dkim" in skip:
            raise Skip("dkim disabled (SMOKE_SKIP)")
        if "sub" not in state:
            raise Skip("no submission message to inspect")
        v = state["sub"].get("DKIM-Signature")
        if not v:
            raise RuntimeError("no DKIM-Signature header")
        return " ".join(v.split())[:60]
    if created_mbox:
        step("header: DKIM-Signature")(s_dkim_hdr)

    def s_spam_hdr():
        if "rspamd" in skip:
            raise Skip("rspamd disabled (SMOKE_SKIP)")
        msgs = [state[k] for k in ("p25", "sub") if k in state]
        if not msgs:
            raise Skip("no delivered message to inspect")
        for m in msgs:
            v = m.get("X-Spamd-Result") or m.get("X-Rspamd-Result")
            if v:
                return " ".join(v.split())[:60]
        raise RuntimeError("no X-Spamd-Result / X-Rspamd-Result header")
    if created_mbox:
        step("header: X-Spamd-Result")(s_spam_hdr)

    if a.keep:
        ROWS.append(("cleanup", "SKIP", 0.0, "SMOKE_KEEP set"))
    else:
        if created_mbox:
            step("cleanup: delete mailbox")(
                lambda: api.post_ok("/api/v1/delete/mailbox", [user]) and user)
        if created_domain:
            step("cleanup: delete domain")(
                lambda: api.post_ok("/api/v1/delete/domain", [a.domain]) and a.domain)

    w = max(len(r[0]) for r in ROWS)
    print(f"{'STEP'.ljust(w)}  RESULT  {'TIME':>7}  DETAIL")
    for n, st, t, d in ROWS:
        print(f"{n.ljust(w)}  {st:<6}  {t:6.2f}s  {d}")
    c = {k: sum(1 for r in ROWS if r[1] == k) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\n{c['PASS']} pass, {c['FAIL']} fail, {c['SKIP']} skip; "
          f"total {sum(r[2] for r in ROWS):.1f}s")
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    import email.message, email.utils  # noqa: F401 (submodules used above)
    sys.exit(main())
