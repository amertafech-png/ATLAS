"""ATLAS self-update: checks GitHub Releases, verifies and runs the platform installer in update mode.

Flow:  check()  ->  state["available"]  ->  install():  download asset -> verify SHA-256 against the release's
SHA256SUMS asset (or checksum lines in the release notes) -> refuse if missing/mismatch -> start the installer with
its non-interactive update flag -> ask the bridge to exit (the installer restarts it).
Nothing is downloaded or run unless the user clicks "Install update" in the HUD.
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout

import atlas_meta

SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")   # plain x.y.z only; pre-release tags are ignored
MAX_INSTALLER_BYTES = 80 * 1024 * 1024
MIN_NETWORK_GAP = 30          # seconds between real requests, even for "Check now"
STALE_CONDITIONAL = 30 * 60   # at startup, revalidate (ETag, free) if the cache is older than this
OFFICIAL_HOSTS = ("github.com", "githubusercontent.com")


def parse_ver(tag):
    m = SEMVER.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag, current):
    a, b = parse_ver(tag), parse_ver(current)
    return bool(a and b and a > b)


def platform_key():
    forced = os.getenv("ATLAS_UPDATE_PLATFORM")
    if forced in atlas_meta.ASSETS:
        return forced
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return None


def find_checksum(release, asset_name, sums_text):
    """SHA-256 for asset_name from SHA256SUMS text, else from the release notes. None if not published."""
    pat = re.compile(r"(?im)^\s*[`*]*([0-9a-f]{64})[`*]*\s+\*?" + re.escape(asset_name) + r"[`*]*\s*$")
    for text in (sums_text or "", release.get("body") or ""):
        m = pat.search(text)
        if m:
            return m.group(1).lower()
    return None


class Updater:
    def __init__(self, cfg, data_dir: Path, user_config_path: Path, emit, request_exit):
        self.cfg, self.data_dir, self.user_config_path = cfg, Path(data_dir), Path(user_config_path)
        self.emit, self.request_exit = emit, request_exit
        self.cache_file = self.data_dir / "update-cache.json"
        self._lock, self._install_lock = asyncio.Lock(), asyncio.Lock()
        self.stage = None            # None | downloading | verifying | installing | restarting | error
        self.stage_msg = ""
        self.last_error = ""
        self._cache = self._load_cache()
        self._last_net = 0.0

    # ---- settings ----
    @property
    def ucfg(self):
        return self.cfg.setdefault("update", {})

    @property
    def enabled(self):
        return bool(self.ucfg.get("enabled", True))

    @property
    def repo(self):
        return os.getenv("ATLAS_UPDATE_REPO") or self.ucfg.get("repo") or atlas_meta.REPO

    @property
    def api_base(self):
        return (os.getenv("ATLAS_UPDATE_API") or self.ucfg.get("api_base") or atlas_meta.API_BASE).rstrip("/")

    @property
    def official(self):
        return self.api_base == atlas_meta.API_BASE

    def set_enabled(self, on):
        self.ucfg["enabled"] = bool(on)
        try:
            cur = json.loads(self.user_config_path.read_text(encoding="utf-8-sig")) if self.user_config_path.exists() else {}
        except Exception:
            cur = {}
        cur.setdefault("update", {})["enabled"] = bool(on)
        self.user_config_path.write_text(json.dumps(cur, indent=2), encoding="utf-8")

    # ---- cache ----
    def _load_cache(self):
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self):
        try:
            self.cache_file.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError:
            pass

    # ---- checking ----
    async def check(self, force=False, startup=False):
        async with self._lock:
            c = self._cache
            now = time.time()
            same_repo = c.get("repo") == self.repo
            age = now - c.get("checked_at", 0) if same_repo else 1e12
            if not self.enabled and not force:
                return self.state()
            if now < c.get("backoff_until", 0) and same_repo:
                return self.state()
            limit = self.ucfg.get("check_hours", 12) * 3600
            if startup:
                limit = STALE_CONDITIONAL
            if not force and same_repo and age < limit:
                return self.state()
            if force and now - self._last_net < MIN_NETWORK_GAP:
                return self.state()
            await self._fetch(same_repo)
            return self.state()

    async def _fetch(self, same_repo):
        self._last_net = time.time()
        url = f"{self.api_base}/repos/{self.repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"ATLAS/{atlas_meta.VERSION}",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if same_repo and self._cache.get("etag"):
            headers["If-None-Match"] = self._cache["etag"]
        c = self._cache
        try:
            async with ClientSession(timeout=ClientTimeout(total=12)) as s:
                async with s.get(url, headers=headers) as r:
                    if r.status == 200:
                        j = await r.json()
                        c.update({"repo": self.repo, "etag": r.headers.get("ETag", ""), "checked_at": time.time(), "error": "",
                                  "no_release": False, "backoff_until": 0, "release": self._trim(j)})
                    elif r.status == 304:
                        c.update({"checked_at": time.time(), "error": ""})
                    elif r.status == 404:
                        c.update({"repo": self.repo, "checked_at": time.time(), "no_release": True, "release": None,
                                  "etag": "", "error": ""})
                    elif r.status in (403, 429):
                        reset = r.headers.get("X-RateLimit-Reset")
                        wait = max(300, min(3600, int(reset) - int(time.time()))) if reset and reset.isdigit() else 3600
                        c.update({"repo": self.repo, "backoff_until": time.time() + wait, "error": "GitHub rate limit - will retry later"})
                    else:
                        c.update({"error": f"GitHub answered HTTP {r.status}"})
        except (ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
            c.update({"error": f"couldn't reach GitHub ({type(e).__name__})"})
        c.setdefault("repo", self.repo)
        self._save_cache()
        self.emit("update", state=self.state())

    @staticmethod
    def _trim(j):
        return {"tag": j.get("tag_name", ""), "name": j.get("name") or j.get("tag_name", ""), "body": (j.get("body") or "")[:8000],
                "url": j.get("html_url", ""), "published": j.get("published_at", ""), "prerelease": bool(j.get("prerelease")),
                "draft": bool(j.get("draft")),
                "assets": [{"name": a.get("name", ""), "size": a.get("size", 0), "url": a.get("browser_download_url", "")}
                           for a in (j.get("assets") or [])]}

    # ---- public state ----
    def state(self):
        c = self._cache if self._cache.get("repo") == self.repo else {}
        rel = c.get("release")
        cur = atlas_meta.VERSION
        st = {"enabled": self.enabled, "current": cur, "repo": self.repo, "checked_at": c.get("checked_at", 0),
              "available": False, "latest": cur, "tag": "", "notes": "", "url": "", "can_install": False,
              "installing": self.stage, "install_message": self.stage_msg}
        err = c.get("error", "")
        if rel and not rel.get("draft") and not rel.get("prerelease") and is_newer(rel["tag"], cur):
            plat = platform_key()
            asset = next((a for a in rel["assets"] if plat and a["name"] == atlas_meta.ASSETS[plat]), None)
            st.update(available=True, latest=rel["tag"].lstrip("v"), tag=rel["tag"], notes=rel["body"], url=rel["url"],
                      can_install=bool(asset), published=rel.get("published", ""))
            st["status"] = "available"
            st["message"] = f"Update available: v{st['latest']}" + ("" if asset else " (no installer for this system in the release)")
        elif rel:
            st.update(latest=(rel["tag"] or cur).lstrip("v"), status="up_to_date", message=f"ATLAS is up to date (v{cur})")
        elif c.get("no_release"):
            st.update(status="up_to_date", message=f"ATLAS is up to date (v{cur}) - no releases published yet")
        elif err:
            st.update(status="error", message="Couldn't check for updates")
        elif not self.enabled:
            st.update(status="disabled", message="Automatic update checks are off")
        else:
            st.update(status="unknown", message="Not checked yet")
        if err and st["status"] != "available":
            st["detail"] = err
        if not self.enabled and st["status"] not in ("available",):
            st["status"], st["message"] = "disabled", "Automatic update checks are off"
        return st

    async def run(self):
        """Background loop: check shortly after startup, then whenever the cache has gone stale."""
        await asyncio.sleep(15)
        first = True
        while True:
            try:
                await self.check(startup=first)
            except Exception as e:  # noqa
                print(f"[update] check failed: {type(e).__name__}: {e}", flush=True)
            first = False
            await asyncio.sleep(3600)

    # ---- install ----
    def _set_stage(self, stage, msg=""):
        self.stage, self.stage_msg = stage, msg
        self.emit("update", state=self.state())

    async def install(self):
        """Download + verify + start installer. Raises RuntimeError(message) on refusal. Returns when the installer is running."""
        if self._install_lock.locked():
            raise RuntimeError("An update is already in progress.")
        async with self._install_lock:
            try:
                return await self._install()
            except Exception as e:
                self._set_stage("error", str(e))
                self.last_error = str(e)
                raise

    async def _http_get(self, session, url, limit):
        """GET url fully into memory (size-capped); on the official API only https GitHub hosts are accepted."""
        if self.official:
            u = urlparse(url)
            if u.scheme != "https" or not any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in OFFICIAL_HOSTS):
                raise RuntimeError("Refusing to download from an unexpected address.")
        async with session.get(url, headers={"User-Agent": f"ATLAS/{atlas_meta.VERSION}"}, allow_redirects=True) as r:
            if r.status != 200:
                raise RuntimeError(f"Download failed (HTTP {r.status}).")
            if self.official:
                fu = urlparse(str(r.url))
                if fu.scheme != "https":
                    raise RuntimeError("Refusing a non-HTTPS download.")
            data = bytearray()
            async for chunk in r.content.iter_chunked(256 * 1024):
                data += chunk
                if len(data) > limit:
                    raise RuntimeError("Download is larger than expected; refusing.")
            return bytes(data)

    async def _install(self):
        await self.check(force=True)
        st = self.state()
        if not st["available"]:
            raise RuntimeError("No update is available.")
        plat = platform_key()
        if not plat:
            raise RuntimeError("Self-update is only available on Windows and macOS.")
        rel = self._cache["release"]
        name = atlas_meta.ASSETS[plat]
        asset = next((a for a in rel["assets"] if a["name"] == name), None)
        if not asset:
            raise RuntimeError(f"The release has no {name} file.")
        sums_asset = next((a for a in rel["assets"] if a["name"] == atlas_meta.CHECKSUMS_ASSET), None)
        self._set_stage("downloading", f"Downloading {name} ...")
        async with ClientSession(timeout=ClientTimeout(total=900)) as s:
            sums_text = ""
            if sums_asset:
                sums_text = (await self._http_get(s, sums_asset["url"], 1 << 20)).decode("utf-8", "ignore")
            expected = find_checksum(rel, name, sums_text)
            if not expected:
                raise RuntimeError("The release doesn't publish a SHA-256 checksum for the installer; refusing to install.")
            data = await self._http_get(s, asset["url"], MAX_INSTALLER_BYTES)
        self._set_stage("verifying", "Checking SHA-256 ...")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise RuntimeError("Checksum mismatch - the download was not trusted and has been discarded.")
        tmp = Path(tempfile.mkdtemp(prefix="atlas-update-"))
        path = tmp / name
        path.write_bytes(data)
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
        self._set_stage("installing", f"Installing v{st['latest']} ...")
        self._launch(plat, path)
        try:
            (self.data_dir / "update-last.json").write_text(json.dumps(
                {"from": atlas_meta.VERSION, "to": st["latest"], "at": time.time(), "installer": str(path)}), encoding="utf-8")
        except OSError:
            pass
        self._set_stage("restarting", "ATLAS is restarting ...")
        asyncio.get_running_loop().call_later(2.0, self.request_exit)
        return {"ok": True, "to": st["latest"]}

    def _launch(self, plat, path: Path):
        if plat == "windows":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(f'cmd.exe /c ""{path}" /update"', creationflags=flags, close_fds=True, cwd=str(path.parent))
        else:
            logdir = self.data_dir / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            log = open(logdir / "update.log", "ab")
            subprocess.Popen(["/bin/bash", str(path), "--update"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True, cwd=str(path.parent))

    def cleanup_old(self):
        base = Path(tempfile.gettempdir())
        for d in base.glob("atlas-update-*"):
            try:
                if time.time() - d.stat().st_mtime > 86400 and d.is_dir():
                    for f in d.iterdir():
                        f.unlink()
                    d.rmdir()
            except OSError:
                pass
