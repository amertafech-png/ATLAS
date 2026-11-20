"""Dev-only (v1.4.3): config.json writes are atomic and merge-safe (pure Python, temp folder).
   python3 bridge/dev/test_cfgio.py"""
import json, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_cfgio as C
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,))); ok or fails.append(n)
d = Path(tempfile.mkdtemp()); f = d / "config.json"
LAY = {"L1": "clock", "L2": "weather", "L3": "calendar", "R1": "sports", "R2": "system", "R3": "hermes"}
f.write_text(json.dumps({"prefs": {"layout": LAY}, "calendar": {"sources": [{"id": "work"}]}}))
def deep(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict): deep(base[k], v)
        else: base[k] = v
    return base
# 40 threads each writing their own key at the same time (HUD settings + brief + features + calendar all at once)
ts = [threading.Thread(target=lambda i=i: C.update_config(f, lambda c: deep(c, {"prefs": {"k%d" % i: i}}))) for i in range(40)]
[t.start() for t in ts]; [t.join() for t in ts]
cur = json.loads(f.read_text())
check("40 simultaneous writers: every change kept", all(cur["prefs"].get("k%d" % i) == i for i in range(40)), cur["prefs"])
check("... and the layout + calendar links untouched", cur["prefs"]["layout"] == LAY and cur["calendar"]["sources"] == [{"id": "work"}])
check("no temp files left behind", not [p for p in d.iterdir() if ".tmp-" in p.name], list(d.iterdir()))
# a half-written / unreadable file is never replaced by {}
f.write_text('{"prefs": {"layout": {"L1": "clock", "L2": "wea')
try:
    C.update_config(f, lambda c: deep(c, {"prefs": {"brief_last": "2026-11-20"}})); refused = False
except C.ConfigUnreadable:
    refused = True
check("an unreadable config.json is not overwritten (change refused)", refused and f.read_text().startswith('{"prefs": {"layout": {"L1": "clock"'), f.read_text()[:80])
check("a copy of the unreadable file is kept", any(p.name.startswith("config.json.unreadable-") for p in d.iterdir()))
f.write_text("")
C.update_config(f, lambda c: c.update(x=1))
check("a config.json that stays empty (nothing in it to lose) is written normally", json.loads(f.read_text()) == {"x": 1})
g = d / "new.json"; C.update_config(g, lambda c: c.update(a=1))
check("a missing file is created", json.loads(g.read_text()) == {"a": 1})
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
