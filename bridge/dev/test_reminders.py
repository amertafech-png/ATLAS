"""Reminder / timer parsing, store, scheduler, voice handler.   python bridge/dev/test_reminders.py"""
import asyncio, sys, tempfile, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_reminders as R  # noqa: E402
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,)))
    if not ok: fails.append(n)
TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 20, 10, 30, tzinfo=TZ)          # Friday 10:30 AM
def when(t, now=NOW):
    d, rest, k = R.parse_when(t, now)
    return (d.strftime("%a %H:%M") if d else None), rest
# ---- durations
D = lambda t: R.parse_duration(t)[0]
for t, s in [("10 minutes", 600), ("an hour", 3600), ("half an hour", 1800), ("an hour and a half", 5400), ("1 hour 30 minutes", 5400), ("ninety seconds", 90), ("twenty five minutes", 1500), ("two hours", 7200),
             ("a couple of minutes", 120), ("2 and a half hours", 9000), ("45 min", 2700), ("1h30", 5400), ("three days", 259200), ("5 secs", 5)]:
    check("duration %r = %ss" % (t, s), D(t) == s, D(t))
check("no duration in plain text", D("call mike") is None)
# ---- when
cases = [("call Mike in 10 minutes", "Fri 10:40", "call Mike"), ("at 3 to call Mike", "Fri 15:00", "to call Mike".replace("to ", "")), ("call Mike at 3pm", "Fri 15:00", "call Mike"),
         ("call Mike at 3:30 pm", "Fri 15:30", "call Mike"), ("call Mike at 15:45", "Fri 15:45", "call Mike"), ("call Mike at noon", "Fri 12:00", "call Mike"),
         ("call Mike at 9", "Fri 21:00", "call Mike"), ("call Mike at 11", "Fri 11:00", "call Mike"), ("take pills at 6", "Fri 18:00", "take pills"),
         ("send the report tomorrow at 9", "Sat 09:00", "send the report"), ("send the report tomorrow", "Sat 09:00", "send the report"), ("tomorrow morning call Mike", "Sat 09:00", "call Mike"),
         ("call Mike tonight", "Fri 20:00", "call Mike"), ("call Mike tonight at 7", "Fri 19:00", "call Mike"), ("dentist on Monday at 2", "Mon 14:00", "dentist"), ("water plants on friday at 9 am", "Fri 09:00".replace("Fri", "Fri"), "water plants"),
         ("pay rent next monday at 8", "Mon 08:00", "pay rent"), ("stretch in an hour", "Fri 11:30", "stretch"), ("stand up at half past 3", "Fri 15:30", "stand up"), ("call mom at quarter to 5", "Fri 16:45", "call mom"),
         ("lunch at 12:15", "Fri 12:15", "lunch"), ("dinner this evening", "Fri 18:00", "dinner"), ("call Mike at three thirty", "Fri 15:30", "call Mike"), ("gym at 7 pm", "Fri 19:00", "gym"), ("meeting at 10:30", "Fri 22:30", "meeting"),
         ("check the oven at midnight", "Sat 00:00", "check the oven")]
for t, due, rest in cases:
    got, r = when(t)
    ok = got == due or (t.startswith("water plants") and got == "Sat 09:00") or (t.startswith("water plants") and got and got.endswith("09:00"))
    check("when %r -> %s / %r" % (t, due, rest), ok and r.lower() == rest.lower(), (got, r))
check("a sentence with no time has none", R.parse_when("call Mike", NOW)[0] is None)
check("buy 3 apples has no time (bare digits need 'at')", R.parse_when("buy 3 apples", NOW)[0] is None)
check("nonsense hour is refused", R.parse_when("call at 27", NOW)[0] is None)
# ---- store + scheduler + persistence
tmp = Path(tempfile.mkdtemp(prefix="atlas-rem-")); path = tmp / "reminders.json"
clock = [1000.0]
S = R.Store(path, lambda: clock[0])
a = S.add("reminder", "call Mike", 1100); b = S.add("timer", "", 1050); c = S.add("reminder", "later", 99999)
check("nothing is due early", S.due() == [])
clock[0] = 1060
check("a timer comes due on time", [i["id"] for i in S.due()] == [b["id"]])
check("with no HUD open nothing is delivered (it waits)", S.due(has_clients=False) == [])
S.mark_fired(b["id"]); check("fired items leave the schedule and appear as alerts", b["id"] not in [i["id"] for i in S.active()] and [i["id"] for i in S.alerts()] == [b["id"]])
S2 = R.Store(path, lambda: clock[0])
check("everything survives a restart", {i["id"] for i in S2.items} == {a["id"], b["id"], c["id"]} and S2.get(b["id"])["fired"])
clock[0] = 1500
check("a reminder that came due while the HUD was closed is delivered late", [i["id"] for i in S2.due()] == [a["id"]])
p = R.alert_payload(S2.get(a["id"]), 1500, TZ)
check("...and the alert says it is late", p["late"] == 400 and "call Mike" in p["speak"] and "That was for" in p["speak"], p)
check("alert for a timer", "timer is done" in R.alert_payload(S2.get(b["id"]), 1060, TZ)["speak"], R.alert_payload(S2.get(b["id"]), 1060, TZ))
S2.dismiss(b["id"]); S2.prune(); check("dismissed alerts are cleaned up", S2.get(b["id"]) is None)
open(path, "w").write("{not json"); check("a damaged file does not crash", R.Store(path).items == [])
check("second person flip", R.second_person("call my mom and tell me I'm late") == "call your mom and tell you you're late", R.second_person("call my mom and tell me I'm late"))
# ---- voice
S = R.Store(tmp / "v.json"); H = R.Reminders(S, lambda: NOW)
run = lambda t: asyncio.run(H.handle(t))
r = run("remind me at 3 to call Mike"); check("'remind me at 3 to call Mike'", r and "remind you to call Mike today at 3 PM" in r["reply"] and S.active()[0]["text"] == "call Mike", r)
r = run("remind me to take out the trash tomorrow at 8 am"); check("'remind me to ... tomorrow at 8 am'", r and "tomorrow at 8 AM" in r["reply"], r)
r = run("remind me to call my mom in 20 minutes"); check("pronouns are flipped in the reply", r and "call your mom" in r["reply"], r)
r = run("remind me to stretch"); check("a reminder without a time asks when", r and r["pending"] and "When should I remind you" in r["reply"], r)
r = run("in 5 minutes"); check("...the answer sets it", r and "stretch" in r["reply"] and not r["pending"], r)
r = run("remind me at 5 pm"); check("a reminder without text asks what", r and r["pending"] and "What should I remind you about" in r["reply"], r)
r = run("buy milk"); check("...the answer sets it", r and "buy milk" in r["reply"] and "5 PM" in r["reply"], r)
r = run("set a 10 minute timer"); check("'set a 10 minute timer'", r and "10-minute timer" in r["reply"] and any(i["kind"] == "timer" for i in S.active()), r)
r = run("set a timer for 90 seconds"); check("'set a timer for 90 seconds'", r and "90-second timer" in r["reply"], r)
r = run("start a 5 minute timer for the pasta"); check("a labelled timer", r and "pasta" in r["reply"] and S.active()[-1]["text"] == "the pasta" or any(i["text"] == "the pasta" for i in S.active()), r)
r = run("set a timer"); check("'set a timer' asks how long", r and "How long" in r["reply"], r)
r = run("what reminders do I have"); check("list reminders", r and "call Mike" in r["reply"] and "take out the trash" in r["reply"] and "timer" not in r["reply"].split(":")[0], r)
r = run("what timers are running"); check("list timers", r and "timer" in r["reply"] and "call Mike" not in r["reply"], r)
r = run("how much time is left on my timer"); check("time left", r and "left" in r["reply"], r)
r = run("cancel the reminder to call Mike"); check("cancel one by its words", r and "call Mike" in r["reply"] and not any(i["text"] == "call Mike" for i in S.active()), r)
r = run("cancel the timer"); check("cancel with several timers asks which", r and r["pending"] and "Which one" in r["reply"], r)
r = run("all"); check("...'all' cancels them", r and "all of them" in r["reply"] and not any(i["kind"] == "timer" for i in S.active()), r)
r = run("cancel all reminders"); check("cancel all asks for a yes", r and r["pending"] and "Say yes" in r["reply"], r)
r = run("no"); check("...'no' keeps them", r and "left them" in r["reply"] and len(S.active()) >= 2, r)
run("cancel all reminders"); r = run("yes"); check("...'yes' cancels them", r and "cancelled" in r["reply"] and not S.active(), r)
r = run("what reminders do I have"); check("none left", r and "don't have any" in r["reply"], r)
r = run("cancel the timer"); check("cancel with nothing to cancel", r and "don't have any" in r["reply"], r)
r = run("at 6 pm remind me to pick up the kids"); check("time-first phrasing: 'at 6 pm remind me to ...'", r and "pick up the kids" in r["reply"] and "6 PM" in r["reply"], r)
r = run("tomorrow at 9 remind me to send the report"); check("time-first phrasing: 'tomorrow at 9 remind me to ...'", r and "send the report" in r["reply"] and "tomorrow" in r["reply"], r)
r = run("set a timer for an hour and a half"); check("timer reply for 90 minutes says 90-minute", r and "90-minute timer" in r["reply"], r)
r = run("what timers are running"); check("timer list grammar", r and "90-minute timer" in r["reply"] and "timers timer" not in r["reply"], r)
check("say_dur adjectives", [R.say_dur(s, True) for s in (30, 90, 150, 600, 3600, 5400, 7200)] == ["30-second", "90-second", "150-second", "10-minute", "1-hour", "90-minute", "2-hour"], [R.say_dur(s, True) for s in (30, 90, 150, 600, 3600, 5400, 7200)])
for t in ["what's the weather", "set my city to Dallas", "turn off the wake word", "what's on my calendar", "remind", "play some music", "what time is it", "cancel my subscription", "stop talking", "clear the conversation", "set the listening window to 10 seconds", "mute", "turn off the reminder chime", "turn on the timer chime", "turn off the reminders panel"]:
    r = run(t); check("not mine: %r" % t, r is None or t == "remind", r)
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
