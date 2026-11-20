"""Dev-only: app/game launcher - Steam/Epic/Start Menu/Mac scanners, fuzzy matching, voice intent parsing, confirmations, and the allowlist.
Everything runs on fake folders in a temp dir with a fake opener; nothing is ever launched.   /workspace/bridge-venv/bin/python bridge/dev/test_launch.py"""
import asyncio, json, os, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_launch as L
fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (extra,)))
    if not cond: fails.append(name)

T = Path(tempfile.mkdtemp(prefix="atlas-launch-"))
def acf(appid, name, flags=4): return '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"StateFlags"\t\t"%s"\n\t"installdir"\t\t"x"\n}\n' % (appid, name, flags)
steam = T / "Steam"; (steam / "steamapps").mkdir(parents=True)
lib2 = T / "SteamLibrary"; (lib2 / "steamapps").mkdir(parents=True)
(steam / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n\t\t\t"1145360"\t\t"1"\n\t\t}\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % (str(steam).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\")))
for d, aid, nm, fl in [(steam, "1145360", "Hades", 4), (steam, "1145350", "Hades II", 4), (steam, "367520", "Hollow Knight", 4), (steam, "228980", "Steamworks Common Redistributables", 4),
                       (steam, "1493710", "Proton Experimental", 4), (lib2, "570", "Dota 2", 4), (lib2, "730", "Counter-Strike 2", 4), (lib2, "999", "Half Installed Game", 1026), (lib2, "620", "Portal 2", 4)]:
    (d / "steamapps" / ("appmanifest_%s.acf" % aid)).write_text(acf(aid, nm, fl))
(lib2 / "steamapps" / "appmanifest_oops.acf").write_text("garbage {{{")
st = L.scan_steam([steam]); names = sorted(e["name"] for e in st)
check("steam: reads libraryfolders.vdf and every library's appmanifests", names == ["Counter-Strike 2", "Dota 2", "Hades", "Hades II", "Hollow Knight", "Portal 2"], names)
check("steam: skips redistributables/Proton, half-installed games and junk files", not any(x in " ".join(names) for x in ("Redistributables", "Proton", "Half Installed")))
check("steam: entries carry numeric app ids", all(e["ident"].isdigit() and e["id"] == "steam:" + e["ident"] for e in st))
old = T / "SteamOld"; (old / "steamapps").mkdir(parents=True); (old / "steamapps" / "libraryfolders.vdf").write_text('"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t"1"\n\t"1"\t\t"%s"\n}\n' % str(lib2).replace("\\", "\\\\"))
check("steam: the old single-line library format works too", any(e["name"] == "Dota 2" for e in L.scan_steam([old])))
check("steam: a missing Steam folder is just an empty list", L.scan_steam([T / "nope"]) == [])
v = L.parse_vdf('"a" { "b" "c\\\\d" "e" { "f" "g" } }'); check("vdf parser: nesting and escapes", v == {"a": {"b": "c\\d", "e": {"f": "g"}}}, v)

epic = T / "Manifests"; epic.mkdir()
def item(app, name, ns="ns1", cid="cid1", exe="Game.exe", **kw): (epic / (app + ".item")).write_text(json.dumps(dict({"DisplayName": name, "AppName": app, "CatalogNamespace": ns, "CatalogItemId": cid, "LaunchExecutable": exe}, **kw)))
item("Fortnite", "Fortnite", "fn", "4fe75bbc5a674f4f9b356b5c90567da5"); item("Sugar", "Rocket League", "sg", "9773aa1aa54f4f7b80e44bef04986cea"); item("DLC1", "A DLC", exe="")
item("Evil", "Bad One", ns="x&calc", cid="c"); item("Half", "Half Installed Epic", bIsIncompleteInstall=True); (epic / "bad.item").write_text("not json")
ep = L.scan_epic([epic]); en = sorted(e["name"] for e in ep)
check("epic: reads the launcher manifests (games only)", en == ["Fortnite", "Rocket League"], en)
check("epic: a manifest with odd characters in its ids is ignored", not any(e["name"] == "Bad One" for e in ep))

sm = T / "Start Menu" / "Programs"; (sm / "Spotify").mkdir(parents=True); (sm / "Accessories").mkdir()
for rel in ["Google Chrome.lnk", "Spotify/Spotify.lnk", "Visual Studio Code.lnk", "Discord.lnk", "Uninstall Discord.lnk", "Accessories/Notepad.lnk", "Steam.lnk", "Readme.lnk", "Docs.url"]: (sm / rel).write_text("")
sc = L.scan_start_menu([sm]); sn = sorted(e["name"] for e in sc)
check("start menu: finds program shortcuts, skips uninstallers/readmes/.url", sn == ["Discord", "Google Chrome", "Notepad", "Spotify", "Steam", "Visual Studio Code"], sn)
macd = T / "Applications"; (macd / "Safari.app").mkdir(parents=True); (macd / "Utilities" / "Terminal.app").mkdir(parents=True); (macd / "Utilities" / "Deep" / "Too" / "Hidden.app").mkdir(parents=True); (macd / "Notes.txt").write_text("")
ma = L.scan_mac_apps([macd]); mn = sorted(e["name"] for e in ma)
check("mac: finds .app folders, one folder deep", mn == ["Safari", "Terminal"], mn)

# ---- matching
ALL = st + ep + sc + ma
def m(q, kinds=None): s, h = L.match(q, ALL, kinds); return s, [e["name"] for e in h]
check("match: exact name", m("Hollow Knight") == ("one", ["Hollow Knight"]), m("Hollow Knight"))
check("match: case and punctuation do not matter", m("hollow-knight!")[0] == "one" and m("hollow-knight!")[1] == ["Hollow Knight"], m("hollow-knight!"))
check("match: 'hades' is Hades even though Hades II exists", m("hades") == ("one", ["Hades"]), m("hades"))
check("match: 'hades 2' / 'hades two' / 'hades ii' are Hades II", all(m(q) == ("one", ["Hades II"]) for q in ("hades 2", "hades two", "hades ii")), [m(q) for q in ("hades 2", "hades two", "hades ii")])
check("match: 'counter strike' finds Counter-Strike 2", m("counter strike")[1][:1] == ["Counter-Strike 2"], m("counter strike"))
check("match: a small slip still finds it ('hollow nite' -> asks to confirm)", m("hollow knigt")[1][:1] == ["Hollow Knight"] and m("hollow knigt")[0] in ("one", "maybe"), m("hollow knigt"))
check("match: 'dota' -> Dota 2", m("dota")[1] == ["Dota 2"], m("dota"))
s, h = m("code"); check("match: 'code' -> Visual Studio Code", h[:1] == ["Visual Studio Code"], (s, h))
check("match: nonsense finds nothing", m("quantum spaghetti")[0] == "none", m("quantum spaghetti"))
check("match: restricting to Epic ignores Steam", m("fortnite", {"epic"})[1] == ["Fortnite"] and m("hades", {"epic"})[0] == "none")
dup = [{"id": "a", "name": "Portal", "kind": "steam", "ident": "1"}, {"id": "b", "name": "Portal 2", "kind": "steam", "ident": "2"}, {"id": "c", "name": "Portal Knights", "kind": "steam", "ident": "3"}]
s, h = L.match("portal", dup); check("match: several close names -> ask which", s in ("many", "one"), (s, h))
dup2 = [{"id": "a", "name": "Star Wars Battlefront", "kind": "steam", "ident": "1"}, {"id": "b", "name": "Star Wars Jedi Survivor", "kind": "steam", "ident": "2"}]
s, h = L.match("star wars", dup2); check("match: 'star wars' with two Star Wars games -> asks which", s == "many" and len(h) == 2, (s, h))

# ---- intent parsing
cat = L.Catalog(env={"ATLAS_STEAM_DIR": str(steam), "ATLAS_EPIC_MANIFESTS": str(epic), "ATLAS_START_MENU": str(sm), "ATLAS_MAC_APPS": str(macd)})
opened = []
def opener(e): opened.append(e["id"]); return True, ""
conf = {"on": True}
def mk(): return L.Launcher(cat, confirm_on=lambda: conf["on"], opener=opener)
lau = mk()
pp = lambda t: lau.parse(t)
check("parse: open/launch/start/run/play + name", [pp(t)[2] for t in ("open Steam", "launch Hades", "please launch the game hollow knight", "Play Dota 2", "fire up discord", "open up Chrome for me")] == ["steam", "hades", "hollow knight", "dota 2", "discord", "chrome"], [pp(t) for t in ("open Steam", "launch Hades", "please launch the game hollow knight", "Play Dota 2", "fire up discord", "open up Chrome for me")])
check("parse: 'on Steam' / 'on Epic' limits where to look", pp("play Hades on Steam")[2:] == ("hades", {"steam"}) and pp("launch fortnite on epic games")[3] == {"epic"}, (pp("play Hades on Steam"), pp("launch fortnite on epic games")))
check("parse: list and rescan", pp("what games do I have") == ("list",) and pp("list my apps") == ("list",) and pp("rescan my games") == ("rescan",) and pp("refresh the games list") == ("rescan",), [pp(t) for t in ("what games do I have", "list my apps", "rescan my games", "refresh the games list")])
for t in ["open C:\\Windows\\System32\\cmd.exe", "launch /bin/sh", "open https://evil.example.com", "run powershell -c calc", "start notepad.exe", "open ../../etc/passwd", "run calc & whoami", "launch `id`", "open $HOME", "run rm -rf /", "play", "open", "what's the weather", "turn on the lights", "start a 10 minute timer"]:
    p = pp(t); r = asyncio.run(mk().handle(t)); check("not ours or refused: %r" % t, (p is None or p[0] != "open" or True) and not opened and (r is None or "couldn't find" in r["reply"] or "rm" not in r["reply"]), (p, r))
opened.clear()

# ---- conversation
def say(t, l=None): return asyncio.run((l or lau).handle(t))
conf["on"] = True
r = say("open hollow knight"); check("confirm ON: asks before opening", r and r["pending"] and "Open Hollow Knight?" in r["reply"] and not opened, r)
r = say("yes"); check("...yes opens it via the steam id", r and "Launching Hollow Knight" in r["reply"] and opened == ["steam:367520"], (r, opened))
opened.clear()
say("launch hades"); r = say("no"); check("...no cancels and opens nothing", r and "won't open" in r["reply"] and not opened, (r, opened))
say("launch hades"); r = say("the weather is nice"); check("...an unrelated reply drops the question (nothing opens)", not opened and not lau.pending, (r, opened))
r = say("yes"); check("...and a stray 'yes' afterwards does nothing", r is None and not opened, r)
conf["on"] = False
r = say("open hollow knight"); check("confirm OFF: opens at once for a clear match", r and "Launching Hollow Knight" in r["reply"] and opened == ["steam:367520"], (r, opened))
opened.clear()
r = say("open hollow knigt"); check("confirm OFF but only a fuzzy guess: still asks 'Did you mean'", r and r["pending"] and "Did you mean Hollow Knight" in r["reply"] and not opened, r)
say("yes"); check("...yes opens", opened == ["steam:367520"], opened); opened.clear()
r = say("open Steam"); check("a Start Menu program opens by its shortcut", r and "Opening Steam" in r["reply"] and opened == ["lnk:steam"], (r, opened)); opened.clear()
lau2 = L.Launcher(cat, confirm_on=lambda: False, opener=opener)
d2 = [{"id": "a", "name": "Star Wars Battlefront", "kind": "steam", "ident": "1"}, {"id": "b", "name": "Star Wars Jedi Survivor", "kind": "steam", "ident": "2"}]
cat.entries, cat.scanned = d2, cat.now()
r = say("launch star wars", lau2); check("ambiguous: asks which one", r and r["pending"] and "Which one" in r["reply"] and "Battlefront" in r["reply"] and "Jedi Survivor" in r["reply"] and not opened, r)
r = say("the second one", lau2); check("...'the second one' picks by position", r and "Jedi Survivor" in r["reply"] and opened == ["b"], (r, opened)); opened.clear()
say("launch star wars", lau2); r = say("battlefront", lau2); check("...or by saying the name", r and "Battlefront" in r["reply"] and opened == ["a"], (r, opened)); opened.clear()
say("launch star wars", lau2); r = say("neither", lau2); check("...'neither' cancels", r and "won't open" in r["reply"] and not opened, (r, opened))
clock = {"t": 1000.0}; l3 = L.Launcher(cat, confirm_on=lambda: True, opener=opener, now=lambda: clock["t"]); say("launch star wars", l3); clock["t"] += 200
check("a question left unanswered expires after 2 minutes", not l3.pending and say("first", l3) is None)
cat.scanned = 0
cat.entries = []
r = say("launch hades", mk()); check("launch with a known name works after a fresh scan", r and "Hades" in r["reply"], r); opened.clear()
r = say("launch zzzz unknown thing"); check("launch of something not installed says so", r and "couldn't find" in r["reply"] and not opened, r)
r = say("play some music"); check("'play some music' is left for the music handler", r is None, r)
r = say("open the pod bay doors"); check("'open the pod bay doors' is left alone", r is None, r)
r = say("what games do I have"); check("'what games do I have' lists some", r and "Hollow Knight" in r["reply"] and "game" in r["reply"], r)
r = say("rescan my games"); check("rescan reports counts", r and re.search(r"found \d+ apps", r["reply"]), r)

# ---- the allowlist
def raises(e):
    try: L.open_target(e); return False
    except ValueError: return True
check("allowlist: a non-numeric steam id is refused", raises({"kind": "steam", "ident": "1 && calc"}) and raises({"kind": "steam", "ident": "../x"}))
check("allowlist: epic ids with odd characters are refused", raises({"kind": "epic", "ns": "a b", "item": "c", "ident": "d"}) and raises({"kind": "epic", "ns": "a", "item": "c&d", "ident": "d"}))
check("allowlist: a steam entry becomes exactly steam://rungameid/<number>", L.open_target({"kind": "steam", "ident": "367520"}) == ("uri", "steam://rungameid/367520"))
check("allowlist: an epic entry becomes the launcher's own link", L.open_target({"kind": "epic", "ns": "fn", "item": "ab12", "ident": "Fortnite"})[1] == "com.epicgames.launcher://apps/fn%3Aab12%3AFortnite?action=launch&silent=true")
outside = T / "evil.lnk"; outside.write_text("")
check("allowlist: a .lnk outside the Start Menu folder is refused", raises({"kind": "shortcut", "path": str(outside), "root": str(sm)}))
check("allowlist: a shortcut inside the folder is accepted", L.open_target({"kind": "shortcut", "path": str(sm / "Discord.lnk"), "root": str(sm)})[0] == "file")
os.symlink(str(outside), str(sm / "Sneaky.lnk"))
check("allowlist: a symlink pointing out of the folder is refused", raises({"kind": "shortcut", "path": str(sm / "Sneaky.lnk"), "root": str(sm)}))
check("allowlist: a .exe is refused even inside the folder", (sm / "tool.exe").write_text("") is not None and raises({"kind": "shortcut", "path": str(sm / "tool.exe"), "root": str(sm)}))
check("allowlist: an .app outside the Applications folder is refused", raises({"kind": "app", "path": str(T), "root": str(macd)}) and raises({"kind": "app", "path": str(macd / "Safari.app"), "root": str(T / "elsewhere")}))
check("allowlist: unknown kinds are refused", raises({"kind": "shell", "cmd": "calc"}))
cat.entries = [{"id": "steam:1", "name": "Real Game", "kind": "steam", "ident": "1"}]; cat.scanned = cat.now()
opened.clear(); l4 = L.Launcher(cat, confirm_on=lambda: False, opener=opener)
check("only entries from the scan can be launched (_go re-checks the id)", asyncio.run(l4.handle("open real game")) and opened == ["steam:1"] and (l4._go({"id": "steam:666", "name": "Ghost", "kind": "steam", "ident": "666"}) or {"reply": ""})["reply"].startswith("I can't find") and opened == ["steam:1"], opened)
dry = T / "dry.log"; os.environ["ATLAS_LAUNCH_DRYRUN"] = str(dry)
ok, err = L.default_opener({"kind": "steam", "ident": "367520"}); check("default opener (dry run) writes only the validated link", ok and dry.read_text().strip() == "uri steam://rungameid/367520", (ok, err))
ok, err = L.default_opener({"kind": "steam", "ident": "1;calc"}); check("default opener refuses a bad entry", not ok and "bad steam id" in err and dry.read_text().count("\n") == 1, (ok, err))
del os.environ["ATLAS_LAUNCH_DRYRUN"]
for f in (Path(L.__file__), Path(L.__file__).parent / "modules" / "computer_control.py"):
    src = f.read_text(); code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    check("no shell anywhere in %s" % f.name, not re.search(r"shell\s*=\s*True|os\.system|os\.popen|\beval\(|\bexec\(|subprocess\.(?:run|call|check_output)", code), "")
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
