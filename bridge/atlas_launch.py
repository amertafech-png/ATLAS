"""ATLAS app and game launcher: find what is installed, match what you said, open it - and nothing else.

What it can open (and ONLY these; there is no way to make it run an arbitrary command or path):
  * Steam games       - found by reading Steam's own library files (libraryfolders.vdf + appmanifest_*.acf); opened with steam://rungameid/<number>
  * Epic Games        - found from the Epic launcher's manifests; opened with the Epic launcher's own link
  * Windows programs  - the Start Menu shortcuts (.lnk); opened the way double-clicking the shortcut does
  * Mac apps          - the .app folders in /Applications, ~/Applications and /System/Applications; opened with `open`
The list is built by scanning, so a name you say can only ever be matched against what the scan found.  Opening is always
done from the scanned entry's own fields (never from text you typed), and every path must still sit under a scanned folder.
No command shell is used anywhere: a program is only ever started with a fixed argument list, or by the operating system's own open.

Pure Python, no third-party packages.  Tests: bridge/dev/test_launch.py (fake folders and a fake opener - nothing is launched).
"""
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CACHE_SECS = 600
JUNK_NAME = re.compile(r"\b(uninstall\w*|readme|read me|release notes|manual|help|website|web site|documentation|license|licence|changelog|eula|support|"
                       r"repair|updater?|crash ?reporter|redistributables?|troubleshoot\w*|command prompt|powershell|registry|setup)\b", re.I)
STEAM_SKIP = re.compile(r"steamworks common|proton|steam linux runtime|steamvr performance|redistributable|dedicated server$", re.I)
STEAM_SKIP_IDS = {"228980", "1070560", "1391110", "1493710", "1628350", "1113280", "1245040"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")


# ================================================================ Valve text format (libraryfolders.vdf, appmanifest_*.acf)
def parse_vdf(text):
    """Minimal parser for Valve's KeyValues text format: "key" "value" and "key" { ... }.  Returns nested dicts (keys lower-cased)."""
    toks = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text or "")
    pos = [0]
    def block():
        out = {}
        while pos[0] < len(toks):
            s, br = toks[pos[0]]
            pos[0] += 1
            if br == "}":
                return out
            if br == "{":
                continue
            key = s.lower()
            if pos[0] >= len(toks):
                break
            s2, br2 = toks[pos[0]]
            if br2 == "{":
                pos[0] += 1
                out[key] = block()
            elif br2 == "}":
                return out
            else:
                pos[0] += 1
                out[key] = s2.replace("\\\\", "\\").replace('\\"', '"')
        return out
    return block()


def _read(path, limit=2_000_000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


# ================================================================ roots
def home():
    return Path(os.path.expanduser("~"))


def steam_roots(env=None):
    env = os.environ if env is None else env
    out = []
    if env.get("ATLAS_STEAM_DIR"):
        out.append(Path(env["ATLAS_STEAM_DIR"]))
    if sys.platform.startswith("win"):
        try:
            import winreg  # noqa
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"), (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        v = winreg.QueryValueEx(k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")[0]
                        out.append(Path(v))
                except OSError:
                    pass
        except ImportError:
            pass
        for pf in (env.get("ProgramFiles(x86)"), env.get("ProgramFiles")):
            if pf:
                out.append(Path(pf) / "Steam")
    elif sys.platform == "darwin":
        out.append(home() / "Library" / "Application Support" / "Steam")
    else:
        out += [home() / ".local" / "share" / "Steam", home() / ".steam" / "steam"]
    seen, res = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            res.append(p)
    return res


def epic_manifest_dirs(env=None):
    env = os.environ if env is None else env
    if env.get("ATLAS_EPIC_MANIFESTS"):
        return [Path(env["ATLAS_EPIC_MANIFESTS"])]
    if sys.platform.startswith("win"):
        return [Path(env.get("ProgramData") or r"C:\ProgramData") / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"]
    if sys.platform == "darwin":
        return [home() / "Library" / "Application Support" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"]
    return []


def start_menu_dirs(env=None):
    env = os.environ if env is None else env
    if env.get("ATLAS_START_MENU"):
        return [Path(p) for p in env["ATLAS_START_MENU"].split(os.pathsep) if p]
    if not sys.platform.startswith("win"):
        return []
    out = []
    if env.get("ProgramData"):
        out.append(Path(env["ProgramData"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if env.get("APPDATA"):
        out.append(Path(env["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return out


def mac_app_dirs(env=None):
    env = os.environ if env is None else env
    if env.get("ATLAS_MAC_APPS"):
        return [Path(p) for p in env["ATLAS_MAC_APPS"].split(os.pathsep) if p]
    if sys.platform != "darwin":
        return []
    return [Path("/Applications"), home() / "Applications", Path("/System/Applications")]


# ================================================================ scanners
def scan_steam(roots):
    out, seen_libs, seen_ids = [], set(), set()
    libs = []
    for root in roots:
        root = Path(root)
        libs.append(root)
        vdf = parse_vdf(_read(root / "steamapps" / "libraryfolders.vdf"))
        lf = vdf.get("libraryfolders") or vdf
        for k, v in lf.items():
            if isinstance(v, dict) and v.get("path"):
                libs.append(Path(v["path"]))
            elif isinstance(v, str) and k.isdigit():       # the old format: "1" "D:\\SteamLibrary"
                libs.append(Path(v))
    for lib in libs:
        key = str(lib).lower()
        if key in seen_libs:
            continue
        seen_libs.add(key)
        try:
            files = sorted((lib / "steamapps").glob("appmanifest_*.acf"))
        except OSError:
            continue
        for f in files:
            st = parse_vdf(_read(f, 200_000)).get("appstate") or {}
            appid, name = str(st.get("appid") or ""), (st.get("name") or "").strip()
            if not appid.isdigit() or not name or appid in seen_ids or appid in STEAM_SKIP_IDS or STEAM_SKIP.search(name):
                continue
            try:
                if not (int(st.get("stateflags") or 4) & 4):          # 4 = fully installed
                    continue
            except ValueError:
                pass
            seen_ids.add(appid)
            out.append({"id": "steam:" + appid, "name": name, "kind": "steam", "ident": appid})
    return out


def scan_epic(manifest_dirs):
    out, seen = [], set()
    for d in manifest_dirs:
        try:
            files = sorted(Path(d).glob("*.item"))
        except OSError:
            continue
        for f in files:
            try:
                j = json.loads(_read(f, 400_000))
            except ValueError:
                continue
            name, app, ns, item = (j.get("DisplayName") or "").strip(), j.get("AppName") or "", j.get("CatalogNamespace") or "", j.get("CatalogItemId") or ""
            if not (name and SAFE_ID.match(app) and SAFE_ID.match(ns) and SAFE_ID.match(item)) or j.get("bIsIncompleteInstall") or not j.get("LaunchExecutable"):
                continue
            if app in seen:
                continue
            seen.add(app)
            out.append({"id": "epic:" + app, "name": name, "kind": "epic", "ident": app, "ns": ns, "item": item})
    return out


def scan_start_menu(dirs, max_depth=3):
    out, seen = [], set()
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        base = len(d.parts)
        for root, subs, files in os.walk(d):
            if len(Path(root).parts) - base >= max_depth:
                subs[:] = []
            for fn in sorted(files):
                if not fn.lower().endswith(".lnk"):
                    continue
                name = fn[:-4].strip()
                if not name or JUNK_NAME.search(name) or name.lower() in seen:
                    continue
                seen.add(name.lower())
                out.append({"id": "lnk:" + name.lower(), "name": name, "kind": "shortcut", "path": str(Path(root) / fn), "root": str(d)})
    return out


def scan_mac_apps(dirs, max_depth=2):
    out, seen = [], set()
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        base = len(d.parts)
        for root, subs, files in os.walk(d):
            keep = []
            for s in sorted(subs):
                if s.lower().endswith(".app"):
                    name = s[:-4]
                    if name.lower() not in seen and not JUNK_NAME.search(name):
                        seen.add(name.lower())
                        out.append({"id": "app:" + name.lower(), "name": name, "kind": "app", "path": str(Path(root) / s), "root": str(d)})
                elif len(Path(root).parts) - base + 1 < max_depth:
                    keep.append(s)
            subs[:] = keep
    return out


# ================================================================ matching
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
NUMW = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
FILLER = {"the", "a", "an", "my", "game", "app", "application", "program", "launcher"}


def tokens(s, drop_filler=True):
    s = (s or "").lower().replace("&", " and ").replace("'", "")
    out = []
    for t in re.findall(r"[a-z0-9]+", s):
        t = ROMAN.get(t) or NUMW.get(t) or t
        if drop_filler and t in FILLER:
            continue
        out.append(t)
    return out


def score(query, name):
    q, n = tokens(query), tokens(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qs, ns = set(q), set(n)
    if qs <= ns:
        return max(0.7, 0.95 - 0.05 * (len(n) - len(q)))
    if ns <= qs and len(ns) >= 1 and len(n) >= 1:
        return 0.78
    jq, jn = "".join(q), "".join(n)
    best = difflib.SequenceMatcher(None, jq, jn).ratio() * 0.95
    toks = [max(difflib.SequenceMatcher(None, a, b).ratio() for b in n) for a in q]
    best = max(best, 0.9 * sum(toks) / len(toks) * (0.9 if len(q) != len(n) else 1.0))
    if jn.startswith(jq) and len(jq) >= 4:
        best = max(best, 0.8)
    return best


def match(query, entries, kinds=None):
    """-> (state, [entries]).  state: 'one' (clear winner), 'maybe' (a single fuzzy guess to confirm), 'many' (ask which), 'none'."""
    pool = [e for e in entries if not kinds or e["kind"] in kinds]
    sc = sorted(((score(query, e["name"]), e["name"].lower(), e) for e in pool), key=lambda x: (-x[0], x[1]))
    sc = [x for x in sc if x[0] >= 0.6]
    if not sc:
        return "none", []
    s0 = sc[0][0]
    close = [x for x in sc if s0 - x[0] < 0.15][:4]
    exact = set(tokens(query)) <= set(tokens(sc[0][2]["name"]))          # every word you said is in the name (not a spelling guess)
    if s0 >= 0.85 and exact and (len(sc) == 1 or s0 - sc[1][0] >= 0.08):
        return "one", [sc[0][2]]
    if len(close) == 1:
        return "maybe", [close[0][2]]
    return "many", [x[2] for x in close]


# ================================================================ opening (the allowlist)
def _under(path, root):
    try:
        p, r = os.path.realpath(path), os.path.realpath(root)
        return p == r or p.startswith(r.rstrip("\\/") + os.sep)
    except (OSError, ValueError):
        return False


def open_target(entry):
    """What to hand the operating system for a scanned entry.  Built ONLY from the entry's own validated fields.
    -> ("uri", text) | ("file", path) | ("app", path); raises ValueError for anything that does not look exactly like a scanned entry."""
    k = entry.get("kind")
    if k == "steam":
        if not str(entry.get("ident", "")).isdigit():
            raise ValueError("bad steam id")
        return "uri", "steam://rungameid/%d" % int(entry["ident"])
    if k == "epic":
        ids = [entry.get("ns", ""), entry.get("item", ""), entry.get("ident", "")]
        if not all(SAFE_ID.match(str(i)) for i in ids):
            raise ValueError("bad epic id")
        return "uri", "com.epicgames.launcher://apps/%s%%3A%s%%3A%s?action=launch&silent=true" % tuple(ids)
    if k == "shortcut":
        p = str(entry.get("path", ""))
        if not p.lower().endswith(".lnk") or not _under(p, entry.get("root", "")) or not os.path.isfile(p):
            raise ValueError("shortcut is not inside a Start Menu folder")
        return "file", p
    if k == "app":
        p = str(entry.get("path", ""))
        if not p.endswith(".app") or not _under(p, entry.get("root", "")) or not os.path.isdir(p):
            raise ValueError("app is not inside an Applications folder")
        return "app", p
    raise ValueError("unknown kind")


def default_opener(entry):
    """Really open it.  Returns (ok, error text).  No shell: a program is started with a fixed argument list or os.startfile."""
    try:
        kind, target = open_target(entry)
        dry = os.environ.get("ATLAS_LAUNCH_DRYRUN")           # tests: write what WOULD be opened to this file instead of opening it
        if dry:
            with open(dry, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (kind, target))
            return True, ""
        if sys.platform.startswith("win"):
            os.startfile(target)  # noqa: S606 - a scanned .lnk or a steam:// / epic link built above
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "uri":
            subprocess.Popen(["xdg-open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return False, "I can only open that kind of thing on Windows or Mac."
        return True, ""
    except (ValueError, OSError) as e:
        return False, str(e)


# ================================================================ the catalog
class Catalog:
    def __init__(self, env=None, now=time.time):
        self.env, self.now = env, now
        self.entries, self.scanned, self.lock = [], 0, threading.Lock()

    def scan(self):
        found = (scan_steam(steam_roots(self.env)) + scan_epic(epic_manifest_dirs(self.env))
                 + scan_start_menu(start_menu_dirs(self.env)) + scan_mac_apps(mac_app_dirs(self.env)))
        with self.lock:
            self.entries, self.scanned = found, self.now()
        return found

    def get(self, force=False):
        if force or not self.scanned or self.now() - self.scanned > CACHE_SECS:
            self.scan()
        return list(self.entries)

    def counts(self):
        c = {}
        for e in self.entries:
            c[e["kind"]] = c.get(e["kind"], 0) + 1
        return c

    def find_id(self, eid):
        return next((e for e in self.entries if e["id"] == eid), None)


# ================================================================ voice / chat
SRC_WORDS = {"steam": {"steam"}, "epic": {"epic"}}
OPEN_RX = re.compile(r"^(?:please |can you |could you |would you |hey )*(open|launch|start|run|play|fire up|boot up|load|bring up)(?: up)?(?: the)?(?: my)?\s+(.+?)(?: for me| please| now| real quick)?$", re.I)
LIST_RX = re.compile(r"^(?:what|which|list|show|tell me)(?: (?:are|is))?(?: all)?(?: of)?(?: the| my)? ?(games|apps|applications|programs|software)(?: (?:do )?(?:i|you) (?:have|can (?:open|launch)|could (?:open|launch)))?(?: (?:installed|on (?:this|my) (?:computer|pc|mac)))?$|^(?:what|which) (games|apps|applications|programs) (?:do i have|can you (?:open|launch)|are installed)(?: on (?:this|my) (?:computer|pc|mac))?$", re.I)
RESCAN_RX = re.compile(r"^(?:re-?scan|refresh|update|look for|scan for|find)(?: (?:my|the|new))?(?: list of)?(?: new)?(?: (?:games|apps|applications|programs))(?: list)?(?: again)?$", re.I)
YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|please do|go for it|correct|that one|open it|launch it)\W*$", re.I)
NO = re.compile(r"^(?:no|nope|nah|cancel|never ?mind|stop|forget it|don't|do not|none|neither)\W*$", re.I)
ORD = {"first": 0, "1st": 0, "one": 0, "1": 0, "second": 1, "2nd": 1, "two": 1, "2": 1, "third": 2, "3rd": 2, "three": 2, "3": 2, "fourth": 3, "4th": 3, "four": 3, "4": 3, "last": -1}


def oxford(items):
    items = list(items)
    return items[0] if len(items) == 1 else (" or ".join(items) if len(items) == 2 else ", ".join(items[:-1]) + ", or " + items[-1])


class Launcher:
    """Voice front end: 'open Steam', 'launch Hades', 'play Hollow Knight on Steam'.  Asks when unsure; pending state lasts 2 minutes."""
    TTL = 120

    def __init__(self, catalog, confirm_on=lambda: True, opener=default_opener, now=time.time):
        self.cat, self.confirm_on, self.opener, self.now = catalog, confirm_on, opener, now
        self.pend = None

    @property
    def pending(self):
        return bool(self.pend and self.now() - self.pend["ts"] < self.TTL)

    @pending.setter
    def pending(self, v):
        if not v:
            self.pend = None

    def _r(self, reply, pending=False):
        return {"reply": reply, "changed": [], "ui": None, "pending": pending}

    def parse(self, text):
        """-> ('open', verb, query, kinds) | ('list',) | ('rescan',) | None"""
        low = re.sub(r"\s+", " ", str(text or "").replace("\u2019", "'")).strip(" .!?,").lower()
        if not low or len(low) > 90:
            return None
        if RESCAN_RX.match(low):
            return ("rescan",)
        if LIST_RX.match(low):
            return ("list",)
        m = OPEN_RX.match(low)
        if not m:
            return None
        verb, q = m.group(1), m.group(2).strip()
        if re.search(r"[\\/:;|&$`<>~*%=]|\.\w{2,4}$|\.\.", q):
            return None                                                   # a path, a command, a URL: never ours
        kinds = None
        sm = re.match(r"^(.*?)\s+(?:on|in|from|through|using|via)\s+(steam|epic(?: games)?(?: launcher)?)$", q)
        if sm:
            q, kinds = sm.group(1).strip(), ({"steam"} if sm.group(2).startswith("steam") else {"epic"})
        q = re.sub(r"^(?:the |my )?(?:game|app|application|program)\s+(?:called |named )?", "", q).strip()
        q = re.sub(r"\s+(?:game|app|application|program)$", "", q).strip()
        if not q or len(tokens(q)) > 6:
            return None
        return ("open", verb, q, kinds)

    async def handle(self, text):
        if self.pend and self.now() - self.pend["ts"] >= self.TTL:
            self.pend = None
        if self.pend:
            r = self._answer(text)
            if r:
                return r
            self.pend = None
        p = self.parse(text)
        if not p:
            return None
        if p[0] == "rescan":
            n = len(self.cat.get(force=True))
            c = self.cat.counts()
            return self._r("Done. I found %d app%s and game%s I can open%s." % (n, "" if n == 1 else "s", "" if n == 1 else "s", " (%s)" % ", ".join("%d %s" % (v, k) for k, v in sorted(c.items())) if c else ""))
        if p[0] == "list":
            es = self.cat.get()
            if not es:
                return self._r("I haven't found any apps or games to open yet. I look through Steam, Epic, and your Start Menu or Applications folder.")
            games = sorted(e["name"] for e in es if e["kind"] in ("steam", "epic"))
            apps = sorted(e["name"] for e in es if e["kind"] in ("shortcut", "app"))
            bits = []
            if games:
                bits.append("%d game%s, like %s" % (len(games), "" if len(games) == 1 else "s", ", ".join(games[:6])))
            if apps:
                bits.append("%d app%s, like %s" % (len(apps), "" if len(apps) == 1 else "s", ", ".join(apps[:6])))
            return self._r("I can open " + " and ".join(bits) + ". Just say open, then the name.")
        _, verb, q, kinds = p
        es = self.cat.get()
        state, hits = match(q, es, kinds)
        strict = verb in ("play", "start", "run", "load", "bring up") and not kinds
        if state == "none" or (strict and state != "one"):
            if state == "none" and verb in ("launch",) or (state == "none" and kinds):
                return self._r("I couldn't find a game or app called %s on this computer. Say rescan my games if you just installed it." % q)
            return None                                                   # "play some music", "open the door": not ours
        if state == "one":
            if self.confirm_on():
                self.pend = {"kind": "confirm", "entries": hits, "ts": self.now()}
                return self._r("Open %s? Say yes or no." % hits[0]["name"], True)
            return self._go(hits[0])
        if state == "maybe":
            self.pend = {"kind": "confirm", "entries": hits, "ts": self.now()}
            return self._r("Did you mean %s? Say yes or no." % hits[0]["name"], True)
        self.pend = {"kind": "choose", "entries": hits, "ts": self.now()}
        return self._r("Which one: %s?" % oxford(e["name"] for e in hits), True)

    def _answer(self, text):
        low = re.sub(r"\s+", " ", str(text or "").replace("\u2019", "'")).strip(" .!?,").lower()
        ents = self.pend["entries"]
        if NO.match(low):
            self.pend = None
            return self._r("Okay, I won't open anything.")
        if self.pend["kind"] == "confirm":
            if YES.match(low):
                return self._go(ents[0])
            return None
        words = low.replace("number ", "").replace("the ", "").replace(" one", "").strip()
        if words in ORD or low in ORD:
            i = ORD.get(words, ORD.get(low))
            if -1 <= i < len(ents):
                return self._go(ents[i])
        if YES.match(low) and len(ents) == 1:
            return self._go(ents[0])
        st, hits = match(low, ents)
        if st in ("one", "maybe") or (st == "many" and hits and score(low, hits[0]["name"]) - score(low, hits[1]["name"]) > 0.05):
            return self._go(hits[0])
        return None

    def _go(self, entry):
        self.pend = None
        cur = self.cat.find_id(entry["id"])
        if not cur:
            return self._r("I can't find %s any more. Say rescan my games." % entry["name"])
        ok, err = self.opener(cur)
        if not ok:
            return self._r("I couldn't open %s. %s" % (cur["name"], err.rstrip(".") + "." if err else ""))
        return self._r(("Launching %s." if cur["kind"] in ("steam", "epic") else "Opening %s.") % cur["name"])
