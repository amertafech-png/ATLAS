"""Dev-only (v1.4.3): calendar questions by chat/voice use ATLAS's own calendar, never Hermes' Google connectors.
Real bridge + mock Hermes; the mock logs exactly what the bridge sent.     /workspace/bridge-venv/bin/python bridge/dev/test_calendar_chat_e2e.py"""
import json, os, sys
from datetime import datetime, timedelta
from pathlib import Path
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"
KEYTXT = "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=test-key-0123456789abcdef\nOPENROUTER_API_KEY=placeholder-not-a-secret\n"

def ics_for_last_week():
    today = datetime.now().date()
    d = today - timedelta(days=today.weekday() + 5)          # Wednesday of last week
    return ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a1\nDTSTART:%s\nDTEND:%s\nSUMMARY:Quarterly board review\nEND:VEVENT\nEND:VCALENDAR\n"
            % (d.strftime("%Y%m%dT150000"), d.strftime("%Y%m%dT160000")))

def chat(text):
    return req("POST", "/api/chat", {"text": text})

def sent(log):
    rows = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return [r for r in rows if r["messages"][0]["role"] == "system"]     # chat turns only (not the bridge's own "two plus two" health probe)

for with_cal in (True, False):
    tag = "with calendar" if with_cal else "no calendar"
    e = Env("calchat" + ("1" if with_cal else "0"), shim=False, env_text=KEYTXT)
    log = e.t / "mock-requests.jsonl"; e.env["MOCK_REQ_LOG"] = str(log)
    cfg = {"profile": {"name": "Adam"}, "prefs": {"skin": "cyan"}}
    if with_cal:
        f = e.t / "adam.ics"; f.write_text(ics_for_last_week())
        cfg["calendar"] = {"sources": [{"id": "work", "name": "Work", "kind": "file", "value": str(f)}]}
    (e.data / "config.json").write_text(json.dumps(cfg))
    try:
        check(tag + ": bridge up", e.up())
        c, j = chat("check last week's calendar")
        if with_cal:
            check(tag + ": 'check last week's calendar' answered by ATLAS", c == 200 and j.get("local") and "Quarterly board review" in j.get("reply", "") and "had 1 event" in j["reply"], j)
        else:
            check(tag + ": no calendar -> points to Settings > Calendar", c == 200 and j.get("local") and "Settings, then Calendar" in j.get("reply", ""), j)
        c, j = chat("can you reconnect my google calendar")
        check(tag + ": reconnect handled by ATLAS, no Google sign-in", c == 200 and j.get("local") and ("already connected" in j["reply"]) == with_cal and "Settings" in j["reply"], j)
        check(tag + ": nothing sent to Hermes so far", sent(log) == [], sent(log))
        c, j = chat("summarize the meetings I had last week and what to prepare from them")
        reqs = sent(log)
        check(tag + ": open-ended calendar question goes to Hermes", c == 200 and len(reqs) == 1, (c, j, len(reqs)))
        if reqs:
            sysm = reqs[0]["messages"][0]["content"]; user = reqs[0]["messages"][-1]["content"]
            check(tag + ": system prompt has the standing calendar/credentials rule", "[ATLAS calendar]" in sysm and "never search the user's files" in sysm, sysm[-400:])
            check(tag + ": request carries ATLAS calendar context", user.startswith("summarize the meetings") and "[ATLAS calendar]" in user, user[:300])
            if with_cal:
                check(tag + ": context lists the event + the no-connectors rule", "Quarterly board review" in user and "Do not use Google" in user, user)
            else:
                check(tag + ": context says no calendar connected", "No calendar is connected" in user, user)
        c, j = chat("tell me a joke")
        reqs = sent(log)
        check(tag + ": non-calendar chat has no calendar context", len(reqs) == 2 and "[ATLAS calendar]" not in reqs[1]["messages"][-1]["content"], reqs[-1:] )
        hist = req("GET", "/api/history")[1]
        check(tag + ": history keeps the user's words only", not any("[ATLAS calendar]" in m["text"] for m in hist.get("history", []) if m["role"] == "user"), str(hist)[:300])
    finally:
        e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
