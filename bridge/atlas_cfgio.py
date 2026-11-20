"""Safe read-modify-write of ATLAS's per-user config.json (v1.4.3).

Before, every writer did read -> change -> write_text().  Two problems:
  * write_text() truncates the file first, so a reader at the same moment (another writer, the HUD's settings request, a crash or power
    loss mid-write) could see an empty/partial file;
  * a writer that could not parse the file (because it caught it half-written, or an antivirus held it) started from {} and then saved
    that - silently wiping the layout, calendar links, profile and everything else.
Now: one lock for all writers in the bridge, the new content goes to a temp file that replaces config.json in one step (os.replace,
retried on Windows while another program holds the file), and if the existing file can't be read it is NOT overwritten
(a copy is kept as config.json.unreadable-<time> and the change is refused)."""
import json
import os
import threading
import time
from pathlib import Path

LOCK = threading.RLock()


class ConfigUnreadable(RuntimeError):
    pass


def _read(path):
    last = None
    for i in range(6):                       # a file being replaced / scanned by antivirus: try again briefly
        try:
            if not path.exists():
                return {}
            txt = path.read_text(encoding="utf-8-sig")
            if not txt.strip():
                last = ValueError("empty file")
            else:
                cur = json.loads(txt)
                if isinstance(cur, dict):
                    return cur
                last = ValueError("not a JSON object")
        except (OSError, ValueError) as e:
            last = e
        time.sleep(0.05 * (i + 1))
    if isinstance(last, ValueError) and str(last) == "empty file":
        return {}                            # still empty after ~1 s: really empty, nothing to lose
    raise ConfigUnreadable("%s could not be read (%s)" % (path, last))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d-%d" % (os.getpid(), threading.get_ident()))
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    for i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:              # Windows: another program has config.json open right now
            time.sleep(0.1 * (i + 1))
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_config(path):
    with LOCK:
        return _read(Path(path))


def update_config(path, change):
    """change(cur_dict) edits the dict in place (or returns a new one).  Returns the saved dict."""
    path = Path(path)
    with LOCK:
        try:
            cur = _read(path)
        except ConfigUnreadable:
            try:
                bak = path.with_name(path.name + ".unreadable-" + time.strftime("%Y%m%d-%H%M%S"))
                if path.exists() and not bak.exists():
                    bak.write_bytes(path.read_bytes())
            except OSError:
                pass
            raise
        new = change(cur)
        if new is None:
            new = cur
        _write(path, new)
        return new
