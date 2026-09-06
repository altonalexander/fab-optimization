"""
Access gate for the public dashboard: access codes and magic links.

nginx asks /auth/check on every request (auth_request) and sends anyone
without a valid session to /login. Two ways in:

  * an ACCESS CODE -- six characters, minted by an admin or by a magic-link
    request. Codes are deliberately shareable; every use is recorded (who,
    when, how many times) so sharing is visible rather than prevented.
  * a MAGIC LINK -- the visitor gives an email, we mint a code tied to that
    email and send it with a one-click link. Not a secure login: it is an
    access code delivered by email, and the record of which email got
    which code is the point.

Admins are sessions whose email ends in @ADMIN_DOMAIN (frontanalytics.com).
They can mint codes, see usage, and disable a code.

Storage is the fab Postgres (tables created on first use). Sessions are a
random id in an HttpOnly cookie, cached in memory so the per-request check
does not hit the database.
"""
import os
import secrets
import smtplib
import sys
import threading
import time
from email.message import EmailMessage
from urllib.parse import quote

from flask import Blueprint, jsonify, make_response, redirect, request

auth_bp = Blueprint("auth", __name__)

ADMIN_DOMAIN   = os.getenv("AUTH_ADMIN_DOMAIN", "frontanalytics.com").lower()
PUBLIC_URL     = os.getenv("PUBLIC_URL", "").rstrip("/")
COOKIE         = "fab_session"
SESSION_DAYS   = int(os.getenv("AUTH_SESSION_DAYS", "30"))
CODE_ALPHABET  = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O/1/I
CODE_LEN       = 6
MAIL_FROM      = os.getenv("MAIL_FROM", "fab-support@frontanalytics.com")
# Mailgun is the house mailer; SMTP stays as a fallback for other setups.
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN  = os.getenv("MAILGUN_DOMAIN", "")
MAILGUN_API_BASE = os.getenv("MAILGUN_API_BASE", "https://api.mailgun.net").rstrip("/")
SMTP_HOST      = os.getenv("SMTP_HOST", "")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER", "")
SMTP_PASS      = os.getenv("SMTP_PASS", "")
# Brute-force brake: attempts per client IP per window. 32^6 codes and a
# handful of live ones make guessing hopeless at this rate anyway.
ATTEMPTS_PER_WINDOW = 20
WINDOW_S = 600


def _pg():
    import psycopg
    return psycopg.connect(
        f"host={os.getenv('PGHOST', 'localhost')} port={os.getenv('PGPORT', '25432')} "
        f"dbname={os.getenv('PGDATABASE', 'fab')} user={os.getenv('PGUSER', 'fab')} "
        f"password={os.getenv('PGPASSWORD', 'fab')} connect_timeout=3", autocommit=True)


_schema_done = False
_schema_lock = threading.Lock()


def _ensure_schema():
    global _schema_done
    if _schema_done:
        return
    with _schema_lock:
        if _schema_done:
            return
        with _pg() as c, c.cursor() as cur:
            cur.execute("""
              CREATE TABLE IF NOT EXISTS access_codes (
                code        text PRIMARY KEY,
                email       text,                       -- magic-link requester, if any
                note        text,
                created_by  text,                       -- admin email or 'magic-link'
                created_at  timestamptz NOT NULL DEFAULT now(),
                first_used_at timestamptz,
                last_used_at  timestamptz,
                use_count   integer NOT NULL DEFAULT 0,
                disabled    boolean NOT NULL DEFAULT false
              );
              CREATE TABLE IF NOT EXISTS sessions (
                id          text PRIMARY KEY,
                code        text REFERENCES access_codes(code),
                email       text,
                created_at  timestamptz NOT NULL DEFAULT now(),
                last_seen   timestamptz NOT NULL DEFAULT now(),
                expires_at  timestamptz NOT NULL,
                user_agent  text,
                ip          text
              );
              CREATE INDEX IF NOT EXISTS sessions_expires ON sessions (expires_at);
            """)
        _schema_done = True


# ---- sessions --------------------------------------------------------------
_cache = {}                     # sid -> (email, code, expires_epoch)
_cache_lock = threading.Lock()


def _client_ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "")


def _session():
    """(email, code) for the request's cookie, or None."""
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    now = time.time()
    with _cache_lock:
        hit = _cache.get(sid)
    if hit and hit[2] > now:
        return hit[0], hit[1]
    try:
        _ensure_schema()
        with _pg() as c, c.cursor() as cur:
            cur.execute("SELECT email, code, extract(epoch from expires_at) FROM sessions "
                        "WHERE id=%s AND expires_at > now()", (sid,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE sessions SET last_seen=now() WHERE id=%s", (sid,))
    except Exception as e:
        print(f"[auth] session lookup failed: {e!r}", file=sys.stderr, flush=True)
        return None
    with _cache_lock:
        _cache[sid] = (row[0], row[1], float(row[2]))
    return row[0], row[1]


def _is_admin(email):
    return bool(email) and email.lower().endswith("@" + ADMIN_DOMAIN)


def _start_session(code, email):
    sid = secrets.token_urlsafe(32)
    _ensure_schema()
    with _pg() as c, c.cursor() as cur:
        cur.execute("INSERT INTO sessions (id, code, email, expires_at, user_agent, ip) "
                    "VALUES (%s,%s,%s, now() + %s * interval '1 day', %s, %s)",
                    (sid, code, email, SESSION_DAYS, request.headers.get("User-Agent", "")[:300],
                     _client_ip()))
        cur.execute("UPDATE access_codes SET use_count = use_count + 1, last_used_at = now(), "
                    "first_used_at = COALESCE(first_used_at, now()) WHERE code=%s", (code,))
    with _cache_lock:
        _cache[sid] = (email, code, time.time() + SESSION_DAYS * 86400)
    return sid


def _set_cookie(resp, sid):
    resp.set_cookie(COOKIE, sid, max_age=SESSION_DAYS * 86400, httponly=True,
                    secure=request.headers.get("X-Forwarded-Proto", request.scheme) == "https",
                    samesite="Lax", path="/")
    return resp


# ---- codes -----------------------------------------------------------------
def _new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def _mint(n, note, created_by, email=None):
    _ensure_schema()
    out = []
    with _pg() as c, c.cursor() as cur:
        for _ in range(n):
            for _try in range(5):
                code = _new_code()
                cur.execute("INSERT INTO access_codes (code, email, note, created_by) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING code",
                            (code, email, note, created_by))
                if cur.fetchone():
                    out.append(code)
                    break
    return out


def _redeem(code):
    """The email tied to a live code, or raise ValueError."""
    code = (code or "").strip().upper().replace("-", "").replace(" ", "")
    if len(code) != CODE_LEN:
        raise ValueError("a code is 6 characters")
    _ensure_schema()
    with _pg() as c, c.cursor() as cur:
        cur.execute("SELECT email, disabled FROM access_codes WHERE code=%s", (code,))
        row = cur.fetchone()
    if not row:
        raise ValueError("unknown code")
    if row[1]:
        raise ValueError("this code has been disabled")
    return code, row[0]


_attempts = {}                  # ip -> [timestamps]


def _throttled(ip):
    now = time.time()
    with _cache_lock:
        ts = [t for t in _attempts.get(ip, []) if now - t < WINDOW_S]
        ts.append(now)
        _attempts[ip] = ts
        return len(ts) > ATTEMPTS_PER_WINDOW


# ---- email -----------------------------------------------------------------
def email_configured():
    return bool(MAILGUN_API_KEY and MAILGUN_DOMAIN) or bool(SMTP_HOST)


def _magic_text(email, code):
    base = PUBLIC_URL or (request.headers.get("X-Forwarded-Proto", request.scheme)
                          + "://" + request.host)
    link = f"{base}/auth/magic?code={code}&email={quote(email)}"
    subject = f"Your fab dashboard access code: {code}"
    text = (f"Your access code for the fab optimization dashboard is:\n\n"
            f"    {code}\n\n"
            f"Open this link to sign in directly:\n\n    {link}\n\n"
            f"Or enter the code at {base}/login. The code stays valid until it is\n"
            f"disabled, so keep it if you plan to come back.\n")
    return subject, text


def _send_magic(email, code):
    subject, text = _magic_text(email, code)
    if MAILGUN_API_KEY and MAILGUN_DOMAIN:
        import base64
        import json as _json
        import urllib.request
        from urllib.parse import urlencode
        data = urlencode({"from": MAIL_FROM, "to": email, "subject": subject,
                          "text": text, "o:tag": "fab-magic-link"}).encode()
        req = urllib.request.Request(
            f"{MAILGUN_API_BASE}/v3/{MAILGUN_DOMAIN}/messages", data=data, method="POST")
        req.add_header("Authorization", "Basic " +
                       base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode())
        with urllib.request.urlopen(req, timeout=20) as r:
            _json.loads(r.read().decode() or "{}")   # raises on non-2xx
        return
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = email
    msg["Subject"] = subject
    msg.set_content(text)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.ehlo()
        if SMTP_PORT != 25:
            s.starttls()
            s.ehlo()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


# ---- routes ----------------------------------------------------------------
@auth_bp.get("/auth/check")
def check():
    """nginx auth_request target: 200 with identity headers, else 401."""
    s = _session()
    if not s:
        return "", 401
    resp = make_response("", 200)
    resp.headers["X-Auth-Email"] = s[0] or ""
    resp.headers["X-Auth-Code"] = s[1] or ""
    return resp


@auth_bp.get("/auth/me")
def me():
    s = _session()
    if not s:
        return jsonify({"authenticated": False, "email_login": email_configured()})
    return jsonify({"authenticated": True, "email": s[0], "code": s[1],
                    "admin": _is_admin(s[0]), "email_login": email_configured()})


@auth_bp.post("/auth/code")
def login_code():
    if _throttled(_client_ip()):
        return jsonify({"error": "too many attempts; try again in a few minutes"}), 429
    body = request.get_json(silent=True) or {}
    try:
        code, email = _redeem(body.get("code"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sid = _start_session(code, email)
    return _set_cookie(jsonify({"ok": True, "email": email, "admin": _is_admin(email)}), sid)


@auth_bp.get("/auth/magic")
def login_magic():
    """The link in the email: redeem the code and land on the dashboard."""
    if _throttled(_client_ip()):
        return "too many attempts; try again in a few minutes", 429
    try:
        code, email = _redeem(request.args.get("code"))
    except ValueError as e:
        return redirect(f"/login?error={quote(str(e))}")
    sid = _start_session(code, email)
    return _set_cookie(redirect("/"), sid)


@auth_bp.post("/auth/email")
def login_email():
    if not email_configured():
        return jsonify({"error": "email sign-in is not configured on this server; "
                                 "use an access code"}), 503
    if _throttled(_client_ip()):
        return jsonify({"error": "too many attempts; try again in a few minutes"}), 429
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 200:
        return jsonify({"error": "that does not look like an email address"}), 400
    code = _mint(1, "magic link", "magic-link", email=email)[0]
    try:
        _send_magic(email, code)
    except Exception as e:
        print(f"[auth] mail to {email} failed: {e!r}", file=sys.stderr, flush=True)
        return jsonify({"error": "could not send the email; try an access code"}), 502
    return jsonify({"ok": True, "sent_to": email})


@auth_bp.get("/auth/logout")
def logout():
    sid = request.cookies.get(COOKIE)
    if sid:
        with _cache_lock:
            _cache.pop(sid, None)
        try:
            with _pg() as c, c.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE id=%s", (sid,))
        except Exception:
            pass
    resp = redirect("/login")
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ---- admin -----------------------------------------------------------------
def _admin_or_403():
    s = _session()
    if not s or not _is_admin(s[0]):
        return None, (jsonify({"error": f"admin access is for @{ADMIN_DOMAIN} sign-ins"}), 403)
    return s[0], None


@auth_bp.get("/auth/admin/codes")
def admin_codes():
    who, err = _admin_or_403()
    if err:
        return err
    _ensure_schema()
    with _pg() as c, c.cursor() as cur:
        cur.execute("""
          SELECT a.code, a.email, a.note, a.created_by, a.created_at, a.first_used_at,
                 a.last_used_at, a.use_count, a.disabled,
                 (SELECT count(*) FROM sessions s WHERE s.code=a.code AND s.expires_at>now())
          FROM access_codes a ORDER BY a.created_at DESC LIMIT 500""")
        rows = cur.fetchall()
    keys = ["code", "email", "note", "created_by", "created_at", "first_used_at",
            "last_used_at", "use_count", "disabled", "live_sessions"]
    return jsonify({"admin": who, "codes": [
        {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(keys, r)}
        for r in rows]})


@auth_bp.post("/auth/admin/codes")
def admin_mint():
    who, err = _admin_or_403()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        n = max(1, min(50, int(body.get("count", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": "count must be a number"}), 400
    note = (body.get("note") or "")[:200]
    return jsonify({"codes": _mint(n, note, who)})


@auth_bp.post("/auth/admin/codes/<code>/disable")
def admin_disable(code):
    who, err = _admin_or_403()
    if err:
        return err
    enable = bool((request.get_json(silent=True) or {}).get("enable"))
    with _pg() as c, c.cursor() as cur:
        cur.execute("UPDATE access_codes SET disabled=%s WHERE code=%s RETURNING code",
                    (not enable, code.upper()))
        if not cur.fetchone():
            return jsonify({"error": "unknown code"}), 404
        if not enable:
            cur.execute("DELETE FROM sessions WHERE code=%s", (code.upper(),))
    with _cache_lock:
        for sid in [k for k, v in _cache.items() if v[1] == code.upper()]:
            _cache.pop(sid, None)
    return jsonify({"ok": True, "code": code.upper(), "disabled": not enable})
