"""Google Calendar for ATLAS, using the sign-in Hermes already keeps for email.

ATLAS does not read mail itself. On Hermes 0.23.2, mail is read by the bundled
google-workspace skill (Gmail API) when this file exists:

  <Hermes home>/google_token.json

Hermes home is HERMES_HOME, or %LOCALAPPDATA%\\hermes on Windows, or ~/.hermes.
The matching OAuth client (the "Google app") is in google_client_secret.json in
that same folder, and is also copied into the token file. A separate Himalaya
setup (~/.config/himalaya/config.toml) is IMAP and cannot open a calendar.

If the token already includes a Calendar scope, ATLAS calls the Calendar API
with it and does not ask Adam to sign in again. The Hermes file is only read.
A refreshed access token for that file stays in memory.

If the token is mail-only, Connect Google reuses that same client, opens the
default browser, and stores a new token in the ATLAS data folder
(%LOCALAPPDATA%\\ATLAS/google_token.json, mode 0600). Nothing here is written
to Git, and tokens, codes and client secrets are never logged.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import atlas_browser

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CAL_LIST = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=8&minAccessRole=reader"
ALLOWED_HOSTS = {"oauth2.googleapis.com", "www.googleapis.com"}
CAL_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CAL_SCOPES = {
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.readonly",
}
EMAIL_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://mail.google.com/",
}
CACHE_SECONDS = 600
MAX_JSON = 65536


class GoogleError(Exception):
    def __init__(self, message, kind="reach"):
        super().__init__(message)
        self.kind = kind


def candidate_homes(environ=None, home=None, windows=None):
    """Hermes homes ATLAS will look in, in order. Pure: tests pass a fake environ."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    windows = (os.name == "nt") if windows is None else bool(windows)
    out = []
    if str(environ.get("HERMES_HOME") or "").strip():
        out.append(Path(environ["HERMES_HOME"]))
    if windows and str(environ.get("LOCALAPPDATA") or "").strip():
        out.append(Path(environ["LOCALAPPDATA"]) / "hermes")
    out.append(home / ".hermes")
    return _dedupe(out)


def _dedupe(paths):
    seen, out = set(), []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def scopes_of(payload):
    raw = payload.get("scopes") if isinstance(payload, dict) else None
    if raw is None and isinstance(payload, dict):
        raw = payload.get("scope")
    if isinstance(raw, str):
        return [s for s in raw.split() if s]
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    return []


def has_calendar(scopes):
    return any(s in CAL_SCOPES for s in scopes)


def has_email(scopes):
    return any(s in EMAIL_SCOPES for s in scopes)


def _safe_scope(s):
    return s.startswith("https://www.googleapis.com/auth/") or s == "https://mail.google.com/"


def read_json(path):
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_JSON or path.stat().st_size <= 0:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def write_private(path, payload):
    """Atomic write, user-readable only. The payload is a token and is never logged."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except GoogleError:
        raise
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise GoogleError("I couldn't save the Google sign-in on this computer.", "save")


def _client_from_token(payload):
    cid, sec = str(payload.get("client_id") or ""), str(payload.get("client_secret") or "")
    if cid and sec:
        return {"client_id": cid, "client_secret": sec}
    return None


def _client_from_secret(payload):
    block = payload.get("installed") or payload.get("web") or {}
    if not isinstance(block, dict):
        return None
    cid, sec = str(block.get("client_id") or ""), str(block.get("client_secret") or "")
    if cid and sec:
        return {"client_id": cid, "client_secret": sec}
    return None


def _parse_expiry(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(value):
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rfc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def default_http(method, url, headers, body, timeout=20):
    """HTTPS to Google only. Inject a fake in tests so nothing is contacted."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in ALLOWED_HOSTS:
        raise GoogleError("Refusing a request that isn't to Google.")
    data = body if isinstance(body, (bytes, type(None))) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 (https host allow-list above)
            raw = res.read(1_000_000)
            code = getattr(res, "status", 200)
    except urllib.error.HTTPError as err:
        raw = err.read(8000)
        code = err.code
    except GoogleError:
        raise
    except Exception:
        raise GoogleError("I couldn't reach Google Calendar just now.")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeError, ValueError):
        payload = {}
    return code, payload if isinstance(payload, dict) else {}


class GoogleCalendar:
    def __init__(self, data_dir, homes=None, http=None, open_url=None, clock=None):
        self.data_dir = Path(data_dir)
        self.homes = list(candidate_homes()) if homes is None else [Path(h) for h in homes]
        self.http = http or default_http
        self.open_url = open_url or atlas_browser.open_default
        self.clock = clock or time.time
        self._memory = {}
        self._cache = None
        self._lock = threading.Lock()
        self._busy = False

    def _owned_path(self):
        return self.data_dir / "google_token.json"

    def _token_paths(self):
        return _dedupe([self._owned_path()] + [Path(h) / "google_token.json" for h in self.homes])

    def _secret_paths(self):
        return _dedupe([self.data_dir / "google_client_secret.json"] + [Path(h) / "google_client_secret.json" for h in self.homes])

    def _load_tokens(self):
        found = []
        owned = self._owned_path()
        for path in self._token_paths():
            data = read_json(path)
            if not data:
                continue
            found.append({"path": path, "data": data, "scopes": scopes_of(data), "owned": path == owned})
        return found

    def _best(self):
        cal = [item for item in self._load_tokens() if has_calendar(item["scopes"])]
        if not cal:
            return None
        owned = [item for item in cal if item["owned"]]
        return owned[0] if owned else cal[0]

    def _client(self):
        tokens = self._load_tokens()
        tokens.sort(key=lambda item: (has_email(item["scopes"]), has_calendar(item["scopes"])), reverse=True)
        for item in tokens:
            client = _client_from_token(item["data"])
            if client:
                return client
        for path in self._secret_paths():
            data = read_json(path)
            client = _client_from_secret(data) if data else None
            if client:
                return client
        return None

    def connected(self):
        item = self._best()
        if not item:
            return False
        data = item["data"]
        token = str(data.get("token") or data.get("access_token") or "")
        exp = _parse_expiry(data.get("expiry"))
        fresh = bool(token) and exp is not None and exp > datetime.now(timezone.utc) + timedelta(seconds=60)
        if fresh:
            return True
        refresh = bool(data.get("refresh_token"))
        return refresh and (_client_from_token(data) is not None or self._client() is not None)

    def needs_connect(self):
        return (not self.connected()) and self._client() is not None

    def status(self):
        """Safe for the HUD and for logs. No token, code, or client secret."""
        item = self._best()
        email = any(has_email(entry["scopes"]) for entry in self._load_tokens())
        connected = self.connected()
        client = self._client() is not None
        if connected and item is not None and not item["owned"]:
            hint = "Same Google sign-in Hermes uses for your email" if email else "Google Calendar on this computer"
        elif connected:
            hint = "Google Calendar, same account as your email" if email else "Google Calendar on this computer"
        elif email:
            hint = "Email sign-in found, calendar not included yet"
        else:
            hint = ""
        return {"connected": connected, "email": email, "needs_connect": (not connected) and client, "client": client, "hint": hint}

    def _access(self, item, force=False):
        key = str(item["path"])
        mem = self._memory.get(key)
        if mem and not force:
            exp = _parse_expiry(mem.get("expiry"))
            if exp and exp > datetime.now(timezone.utc) + timedelta(seconds=30) and mem.get("token"):
                return mem["token"]
        data = item["data"]
        token = str(data.get("token") or data.get("access_token") or "")
        exp = _parse_expiry(data.get("expiry"))
        if token and not force and exp and exp > datetime.now(timezone.utc) + timedelta(seconds=60):
            return token
        refresh = str(data.get("refresh_token") or "")
        client = _client_from_token(data) or self._client()
        if not refresh or not client:
            if token and not force:
                return token
            raise GoogleError("The Google sign-in on this computer needs to be refreshed. Say connect Google.", "auth")
        form = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        }).encode("utf-8")
        code, payload = self.http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, form)
        if code != 200 or not payload.get("access_token"):
            if payload.get("error") == "invalid_grant":
                raise GoogleError("The Google sign-in on this computer has expired. Say connect Google and I'll open a one-time page in your browser.", "auth")
            raise GoogleError("I couldn't refresh the Google sign-in. Say connect Google and I'll open a one-time page in your browser.", "auth")
        access = str(payload["access_token"])
        expires_in = int(payload.get("expires_in") or 3600)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=max(30, expires_in - 60))).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["token"] = access
        data["expiry"] = expiry
        if payload.get("refresh_token"):
            data["refresh_token"] = payload["refresh_token"]
        self._memory[key] = {"token": access, "expiry": expiry}
        if item["owned"]:
            write_private(item["path"], data)
        return access

    def _get(self, token, url):
        code, payload = self.http("GET", url, {"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "ATLAS-calendar/1"}, None)
        if code == 401:
            raise GoogleError("The Google sign-in needs to be refreshed.", "auth")
        if code == 403:
            raise GoogleError("That Google sign-in doesn't include your calendar. Say connect Google and I'll open a one-time page in your browser.", "scope")
        if code != 200:
            raise GoogleError("I couldn't reach Google Calendar just now.")
        return payload

    def _fetch(self, token, start, end):
        tz = start.tzinfo or timezone.utc
        cals = [("primary", "Google")]
        try:
            listing = self._get(token, CAL_LIST)
            found = []
            for entry in (listing.get("items") or [])[:8]:
                if not isinstance(entry, dict) or not entry.get("id") or entry.get("selected") is False:
                    continue
                found.append((str(entry["id"])[:200], str(entry.get("summary") or "Google")[:40]))
            if found:
                cals = found
        except GoogleError as err:
            if err.kind in ("auth", "scope"):
                raise
        out = []
        for cal_id, name in cals:
            query = urllib.parse.urlencode({
                "singleEvents": "true", "orderBy": "startTime", "timeMin": _rfc(start), "timeMax": _rfc(end),
                "maxResults": "40", "showDeleted": "false",
            })
            url = "https://www.googleapis.com/calendar/v3/calendars/%s/events?%s" % (urllib.parse.quote(cal_id, safe=""), query)
            try:
                payload = self._get(token, url)
            except GoogleError as err:
                if err.kind in ("auth", "scope"):
                    raise
                continue
            for item in payload.get("items") or []:
                ev = _event_from(item, name, tz)
                if ev and _overlaps(ev, start, end):
                    out.append(ev)
                if len(out) >= 40:
                    break
            if len(out) >= 40:
                break
        out.sort(key=lambda ev: (ev["start"], ev["title"]))
        return out

    def events(self, start, end, force=False):
        if not self.connected():
            raise GoogleError("Google Calendar isn't connected. Say connect Google.", "scope")
        key = (start.isoformat(), end.isoformat())
        if not force and self._cache and self._cache["key"] == key and self.clock() - self._cache["t"] < CACHE_SECONDS:
            return list(self._cache["events"])
        item = self._best()
        token = self._access(item)
        try:
            evs = self._fetch(token, start, end)
        except GoogleError as err:
            if err.kind != "auth":
                raise
            token = self._access(item, force=True)
            evs = self._fetch(token, start, end)
        self._cache = {"key": key, "t": self.clock(), "events": evs}
        return list(evs)

    def connect(self, timeout=180):
        """Open the default browser and wait for Google to redirect back to this computer."""
        with self._lock:
            if self._busy:
                return {"connected": False, "message": "A Google sign-in is already open in your browser."}
            if self.connected():
                return {"connected": True, "message": "Google Calendar is already connected. I read it with the same Google sign-in as your email, saved only on this computer. Ask me what's on today."}
            client = self._client()
            if not client:
                return {"connected": False, "message": "I can't open a Google sign-in yet because the Google app file Hermes uses isn't on this computer. Open Settings, then Calendar, and add an .ics file or a secret calendar link."}
            self._busy = True
        try:
            return self._connect(client, timeout)
        except GoogleError as err:
            return {"connected": False, "message": str(err)}
        except Exception:
            return {"connected": False, "message": "I couldn't finish connecting Google."}
        finally:
            with self._lock:
                self._busy = False

    def _connect(self, client, timeout):
        box = {"state": None, "code": None, "error": None, "stop": False, "httpd": None, "port": None, "ready": threading.Event()}

        def serve():
            try:
                httpd = HTTPServer(("127.0.0.1", 0), _callback_handler(box))
            except OSError:
                box["error"] = "failed"
                box["ready"].set()
                return
            box["httpd"] = httpd
            box["port"] = httpd.server_address[1]
            httpd.timeout = 0.5
            box["ready"].set()
            deadline = time.time() + max(1, timeout)
            try:
                while time.time() < deadline and not box.get("code") and not box.get("error") and not box.get("stop"):
                    httpd.handle_request()
            finally:
                httpd.server_close()

        thread = threading.Thread(target=serve, name="atlas-google-oauth", daemon=True)
        thread.start()
        if not box["ready"].wait(5) or not box.get("port"):
            return {"connected": False, "message": "I couldn't open a page for the Google sign-in on this computer."}
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(24)
        box["state"] = state
        redirect = "http://127.0.0.1:%d/" % box["port"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        scopes = [CAL_SCOPE]
        for item in self._load_tokens():
            for scope in item["scopes"]:
                if _safe_scope(scope) and scope not in scopes:
                    scopes.append(scope)
        query = urllib.parse.urlencode({
            "client_id": client["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        opened = self.open_url(AUTH_URL + "?" + query)
        ok = opened[0] if isinstance(opened, tuple) else bool(opened)
        if not ok:
            box["stop"] = True
            thread.join(2)
            return {"connected": False, "message": "I couldn't open your browser to connect Google."}
        thread.join(timeout + 2)
        if box.get("error") == "denied":
            return {"connected": False, "message": "Google didn't grant calendar access. Say connect Google if you want to try again."}
        if not box.get("code"):
            return {"connected": False, "message": "I opened Google in your browser, but the sign-in wasn't finished. Say connect Google again when you're ready."}
        return self._exchange(client, box["code"], verifier, redirect, scopes)

    def _exchange(self, client, code, verifier, redirect, requested):
        form = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect,
            "code_verifier": verifier,
        }).encode("utf-8")
        status, payload = self.http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, form)
        if status != 200 or not payload.get("access_token"):
            raise GoogleError("I couldn't finish the Google sign-in. Say connect Google to try again.", "auth")
        granted = [s for s in str(payload.get("scope") or "").split() if _safe_scope(s)] or list(requested)
        if not has_calendar(granted):
            raise GoogleError("Google didn't include calendar access. Say connect Google and allow calendar when the page asks.", "scope")
        refresh = payload.get("refresh_token") or ""
        if not refresh:
            for item in self._load_tokens():
                if str(item["data"].get("client_id") or "") == client["client_id"] and item["data"].get("refresh_token"):
                    refresh = item["data"]["refresh_token"]
                    break
        expires_in = int(payload.get("expires_in") or 3600)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=max(30, expires_in - 60))).strftime("%Y-%m-%dT%H:%M:%SZ")
        token = {
            "type": "authorized_user",
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh,
            "token": payload["access_token"],
            "token_uri": TOKEN_URL,
            "scopes": granted,
            "expiry": expiry,
        }
        write_private(self._owned_path(), token)
        self._cache = None
        self._memory.pop(str(self._owned_path()), None)
        email = any(has_email(item["scopes"]) for item in self._load_tokens()) or has_email(granted)
        if email:
            message = "Google Calendar is connected. I used the same Google app as your email, and the permission stays only on this computer. Ask me what's on today."
        else:
            message = "Google Calendar is connected. The sign-in stays only on this computer. Ask me what's on today."
        return {"connected": True, "message": message}


def _overlaps(ev, start, end):
    if ev["end"] > start and ev["start"] < end:
        return True
    return ev["end"] == ev["start"] and start <= ev["start"] < end


def _event_from(item, cal_name, tz):
    if not isinstance(item, dict) or str(item.get("status") or "").lower() == "cancelled":
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    if not isinstance(start, dict):
        return None
    try:
        if start.get("date") and not start.get("dateTime"):
            st = datetime.strptime(str(start["date"])[:10], "%Y-%m-%d").replace(tzinfo=tz)
            en_s = str((end or {}).get("date") or start["date"])[:10]
            en = datetime.strptime(en_s, "%Y-%m-%d").replace(tzinfo=tz)
            if en <= st:
                en = st + timedelta(days=1)
            all_day = True
        elif start.get("dateTime"):
            st = _parse_dt(start["dateTime"])
            en = _parse_dt(end["dateTime"]) if isinstance(end, dict) and end.get("dateTime") else st
            all_day = False
        else:
            return None
    except (ValueError, TypeError):
        return None
    title = str(item.get("summary") or "(no title)").strip()[:180] or "(no title)"
    return {"title": title, "start": st, "end": en, "all_day": all_day, "location": str(item.get("location") or "").strip()[:180],
            "source": (cal_name or "Google")[:40], "origin": "google"}


def _callback_handler(box):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            self._page(405, "Close this tab and go back to ATLAS.")

        def do_GET(self):
            if len(self.path) > 2048:
                self._page(400, "That sign-in page wasn't valid. Close this tab and try Connect Google again.")
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/", ""):
                self._page(404, "Close this tab and go back to ATLAS.")
                return
            params = urllib.parse.parse_qs(parsed.query, max_num_fields=20)
            err = (params.get("error") or [""])[0]
            if err:
                box["error"] = "denied" if err in ("access_denied", "invalid_scope") else "failed"
                self._page(200, "Google didn't allow calendar. You can close this tab.")
                return
            state = (params.get("state") or [""])[0]
            if not box.get("state") or state != box["state"]:
                self._page(400, "That sign-in didn't match. Close this tab and try Connect Google again.")
                return
            code = (params.get("code") or [""])[0]
            if not code or len(code) > 512:
                self._page(400, "No sign-in code was returned. Close this tab and try again.")
                return
            box["code"] = code
            self._page(200, "Google Calendar is connected. You can close this tab and go back to ATLAS.")

        def _page(self, status, text):
            body = ("<!doctype html><meta charset=utf-8><title>ATLAS</title><p style=\"font-family:sans-serif\">%s</p>" % _escape(text)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
