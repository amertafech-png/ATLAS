#!/usr/bin/env python3
"""Fake Hermes API server for testing the bridge/HUD without Hermes (OpenAI-compatible subset).
Run:  python mock_hermes.py   (listens on 127.0.0.1:8642, key: test-key-0123456789abcdef)"""
import base64, json, sys, time
from aiohttp import web

KEY = "test-key-0123456789abcdef"
# MOCK_HERMES_HOME=<dir>: read the API key from <dir>/.env (API_SERVER_KEY) on every request, like the real gateway would after a restart.
# MOCK_NO_PROVIDER=1: behave like a Hermes that is not signed in to any AI yet.


def current_key():
    d = __import__("os").environ.get("MOCK_HERMES_HOME")
    if d:
        try:
            for line in open(d + "/.env", encoding="utf-8"):
                if line.startswith("API_SERVER_KEY="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            return ""
    return KEY
SESSIONS = {}


ECHOED = {}


def has_retry_nudge(body):
    return any(m.get('role') == 'system' and 'Do not call tools' in str(m.get('content')) for m in body.get('messages', []))


async def health(_):
    return web.json_response({"status": "ok", "platform": "hermes-agent-mock"})


async def models(req):
    if req.headers.get("Authorization") != f"Bearer {current_key()}":
        return web.json_response({"error": {"message": "Invalid API key"}}, status=401)
    return web.json_response({"object": "list", "data": [{"id": "hermes-agent", "object": "model"}]})


async def chat(req):
    if req.headers.get("Authorization") != f"Bearer {current_key()}":
        return web.json_response({"error": {"message": "Invalid API key"}}, status=401)
    if __import__("os").environ.get("MOCK_NO_PROVIDER") == "1" and not __import__("os").path.exists("/tmp/mock_hermes_signed_in"):
        return web.json_response({"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "no provider"}, "finish_reason": "error"}],
                                  "hermes": {"failed": True, "error": "Hermes is not connected to any AI provider yet. Run `hermes model` to pick one."}})
    body = await req.json()
    sid = req.headers.get("X-Hermes-Session-Id", "none")
    turns = SESSIONS.setdefault(sid, [])
    user = body["messages"][-1]["content"]
    has_sys = any(m["role"] == "system" for m in body["messages"])
    text, img = "", None
    if isinstance(user, list):
        for p in user:
            if p.get("type") == "text":
                text += p["text"]
            if p.get("type") == "image_url":
                u = p["image_url"]["url"]
                img = len(base64.b64decode(u.split(",", 1)[1])) if u.startswith("data:image") else -1
    else:
        text = user
    turns.append(text)
    low = text.lower()
    if img:
        reply = f"Mock vision: I received a {img // 1024} kilobyte webcam frame. In the real setup I would describe what is on your desk."
    elif "weather" in low:
        reply = "Mock Hermes here. In the real setup I would check the live forecast for your city and tell you."
    elif "remember" in low or "earlier" in low or "before" in low:
        reply = f"We are on turn {len(turns)} of this session. Your first message was: {turns[0][:60]}"
    else:
        reply = f"Mock Hermes heard: {text[:120]}. This is turn {len(turns)} of session {sid}."
    mode = __import__("os").environ.get("MOCK_ECHO")          # a weak local model: answers with tool-call text ("always") or only the first time ("once")
    if mode == "always" or (mode == "once" and not has_retry_nudge(body) and not ECHOED.get(sid)):
        ECHOED[sid] = True
        reply = "Here is an example of a function call in the format you requested for your question:\nTool: get_current_time\nArguments: {}"
    if len(reply) > 0 and "longreply" in low:
        reply = ("This is a long answer. " * 40).strip()
    await __import__("asyncio").sleep(0.8)
    return web.json_response({
        "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()), "model": "hermes-agent",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        "mock": {"system_prompt_seen": has_sys, "session": sid}}, headers={"X-Hermes-Session-Id": sid})


app = web.Application()
app.add_routes([web.get("/health", health), web.get("/v1/models", models), web.post("/v1/chat/completions", chat)])
if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8642, print=None)
