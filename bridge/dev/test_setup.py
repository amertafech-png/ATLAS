#!/usr/bin/env python3
"""Unit tests for bridge/atlas_hermes.py (no Hermes needed).   python bridge/dev/test_setup.py"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_hermes as m

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

tmp = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(tmp / "fakehome"); os.environ["USERPROFILE"] = str(tmp / "fakehome")   # never touch a real Hermes
os.environ.pop("LOCALAPPDATA", None) if os.name != "nt" else None
# --- .env parsing / writing
env = tmp / ".env"
env.write_text("# my settings\nOPENROUTER_API_KEY=abc12345\nexport API_SERVER_ENABLED=false\nAPI_SERVER_KEY=old\r\nAPI_SERVER_KEY=dup\nFOO=\"bar baz\"\n", encoding="utf-8")
d = m.read_env_file(env)
check("parse: export + quotes + last wins", d["API_SERVER_ENABLED"] == "false" and d["FOO"] == "bar baz" and d["API_SERVER_KEY"] == "dup")
m.set_env_values(env, {"API_SERVER_KEY": "atlas-0123456789abcdef", "API_SERVER_ENABLED": "true", "NEW_ONE": "x1234567"})
d = m.read_env_file(env); txt = env.read_text()
check("write: values set, other lines untouched", d["API_SERVER_KEY"] == "atlas-0123456789abcdef" and d["API_SERVER_ENABLED"] == "true" and d["OPENROUTER_API_KEY"] == "abc12345" and "# my settings" in txt and d["NEW_ONE"] == "x1234567")
check("write: duplicates collapsed", txt.count("API_SERVER_KEY=") == 1)
check("write: backup kept", len(list(tmp.glob(".env.atlas-backup-*"))) == 1)
if os.name != "nt":
    check("write: owner-only permissions", (env.stat().st_mode & 0o077) == 0)
for bad in ("a\nB=1", "has space", "q'uote", ""):
    try: m.set_env_values(env, {"X_KEY": bad}); ok = False
    except ValueError: ok = True
    check("write: refuses unsafe value %r" % bad[:8], ok)
try: m.set_env_values(env, {"bad name": "abcdefgh"}); ok = False
except ValueError: ok = True
check("write: refuses bad variable name", ok)
fresh = tmp / "sub" / ".env"
m.set_env_values(fresh, {"API_SERVER_KEY": "atlas-aaaaaaaaaaaaaaaa"})
check("write: creates new file (and folder), no backup needed", fresh.exists() and not list(fresh.parent.glob("*.atlas-backup-*")))
for i in range(8):
    m.set_env_values(env, {"API_SERVER_KEY": "atlas-%016d" % i})
check("write: backups pruned to 5", len(list(tmp.glob(".env.atlas-backup-*"))) <= 5)
# --- yaml
y = "model:\n  default: x # c\n  provider: \"custom\"\ngateway:\n  other: 1\n  api_server:\n    enabled: true\n    key: 'my-secret-key-123'  # hi\n    port: 8642\napi_server:\n  key: top-level-key-1\n"
check("yaml: nested key", m.yaml_lookup(y, ["gateway", "api_server", "key"]) == "my-secret-key-123")
check("yaml: top-level alt", m.yaml_lookup(y, ["api_server", "key"]) == "top-level-key-1")
check("yaml: model block", m.yaml_lookup(y, ["model", "provider"]) == "custom" and m.yaml_lookup(y, ["model", "default"]) == "x")
check("yaml: absent", m.yaml_lookup(y, ["gateway", "api_server", "nope"]) is None)
check("valid_key", m.valid_key("atlas-abcdef0123") and not m.valid_key("change-me-local-dev") and not m.valid_key("") and not m.valid_key("short"))
# --- key resolution order
home = tmp / "hh"; home.mkdir()
os.environ["HERMES_HOME"] = str(home); os.environ.pop("API_SERVER_KEY", None)
cfg = {"hermes": {"api_url": "http://127.0.0.1:8642", "api_key": "", "read_key_from_hermes_env": True, "model": "hermes-agent", "cli_command": "hermes-not-here"}}
mg = m.HermesManager(cfg, tmp / "data")
check("key: none yet", mg.resolve_api_key() == "" and mg.live_key() == ("", ""))
(home / "config.yaml").write_text("gateway:\n  api_server:\n    enabled: true\n    key: from-yaml-key-123\n")
check("key: found in config.yaml", mg.live_key() == ("from-yaml-key-123", "config.yaml") and mg.api_enabled())
(home / ".env").write_text("API_SERVER_KEY=from-env-key-1234\n")
check("key: .env beats config.yaml", mg.live_key() == ("from-env-key-1234", ".env"))
check("adopt: silently copies it", mg.adopt_key() == ".env" and mg.saved_key() == "from-env-key-1234" and mg.adopt_key() == "")
(home / ".env").unlink(); (home / "config.yaml").unlink()
check("key: saved copy is the last resort", mg.resolve_api_key() == "from-env-key-1234")
cfg["hermes"]["api_key"] = "typed-in-config-1"
check("key: typed key is used when Hermes' files have none", mg.resolve_api_key() == "typed-in-config-1")
(home / ".env").write_text("API_SERVER_KEY=live-wins-0123456\n")
check("key: Hermes' live key beats a stale typed one", mg.resolve_api_key() == "live-wins-0123456")
cfg["hermes"]["read_key_from_hermes_env"] = False
check("key: ...unless read_key_from_hermes_env is false", mg.resolve_api_key() == "typed-in-config-1")
cfg["hermes"]["read_key_from_hermes_env"] = True
(home / ".env").unlink()
cfg["hermes"]["api_key"] = ""
(home / ".env").write_text("API_SERVER_PORT=9999\n")
check("api_url follows Hermes' port", mg.api_url() == "http://127.0.0.1:9999")
# ensure_key_and_api writes a fresh key once
(home / ".env").write_text("OPENROUTER_API_KEY=keep12345\n"); mg.saved_key_file.unlink()
ch = mg.ensure_key_and_api(); k1, _ = mg.live_key()
check("ensure: creates key + enables API", ch == {"key", "enabled"} and len(k1) >= 32 and mg.api_enabled())
check("ensure: keeps other settings, backs up", "OPENROUTER_API_KEY=keep12345" in (home / ".env").read_text() and list(home.glob(".env.atlas-backup-*")))
check("ensure: second call changes nothing", mg.ensure_key_and_api() == set() and mg.live_key()[0] == k1)
# --- redaction
check("redact", "SECRETVALUE1" not in m.redact("x SECRETVALUE1 y Bearer abcdef123456", ["SECRETVALUE1"]) and "abcdef123456" not in m.redact("Authorization: Bearer abcdef123456"))
# --- chat result interpretation
ok = m.interpret_chat(200, json.dumps({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}))
check("chat: ok", ok == {"ok": True, "reply": "hi"})
np = m.interpret_chat(200, json.dumps({"choices": [{"message": {"content": "x"}, "finish_reason": "error"}], "hermes": {"failed": True, "error": "Hermes is not connected to any AI provider yet. Run `hermes model`"}}))
check("chat: no provider detected", np["kind"] == "no_provider")
pa = m.interpret_chat(200, json.dumps({"choices": [{"message": {"content": "x"}, "finish_reason": "error"}], "hermes": {"failed": True, "error": "HTTP 401: Missing Authentication header"}}))
check("chat: bad provider key detected", pa["kind"] == "provider_auth")
check("chat: 401 / down", m.interpret_chat(401, b"")["kind"] == "auth" and m.interpret_chat(0, b"")["kind"] == "unreachable")
# --- CLI discovery outside PATH
fake = tmp / "hh" / "bin"; fake.mkdir()
exe = fake / ("hermes.cmd" if os.name == "nt" else "hermes"); exe.write_text("#!/bin/sh\necho hi\n"); exe.chmod(0o755)
cfg["hermes"]["cli_command"] = "hermes"
old = os.environ.get("PATH", ""); os.environ["PATH"] = "/usr/bin" if os.name != "nt" else old
mg2 = m.HermesManager(cfg, tmp / "data2")
check("cli: found in <HERMES_HOME>/bin although not on PATH", (mg2.find_cli(force=True) or "").endswith(exe.name))
os.environ["PATH"] = old
print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
