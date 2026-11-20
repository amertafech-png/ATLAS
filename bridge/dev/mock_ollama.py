"""Tiny fake Ollama (tags / pull / create / OpenAI-compatible chat) for testing ATLAS's local-model setup.
   python mock_ollama.py [port]   (default 11434)"""
import asyncio, json, sys
from aiohttp import web

MODELS = {"llama3.2:3b"}
LOG = []

async def tags(r):
    return web.json_response({"models": [{"name": m} for m in sorted(MODELS)]})

async def pull(r):
    b = await r.json()
    resp = web.StreamResponse(); resp.content_type = "application/x-ndjson"; await resp.prepare(r)
    for i in range(1, 6):
        await resp.write((json.dumps({"status": "pulling", "total": 100, "completed": i * 20}) + "\n").encode()); await asyncio.sleep(0.2)
    MODELS.add(b["model"]); await resp.write(b'{"status":"success"}\n'); return resp

async def create(r):
    b = await r.json(); MODELS.add(b["model"]); LOG.append(("create", b)); return web.json_response({"status": "success"})

async def models(r):
    return web.json_response({"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})

async def show(r):
    return web.json_response({"model_info": {"llama.context_length": 131072}, "parameters": "num_ctx 65536"})

async def chat(r):
    b = await r.json(); LOG.append(("chat", b.get("model")))
    txt = "ready"
    if b.get("stream"):
        resp = web.StreamResponse(); resp.content_type = "text/event-stream"; await resp.prepare(r)
        for part in ({"choices": [{"index": 0, "delta": {"role": "assistant", "content": txt}}]}, {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}):
            await resp.write(("data: " + json.dumps(part) + "\n\n").encode())
        await resp.write(b"data: [DONE]\n\n"); return resp
    return web.json_response({"id": "x", "object": "chat.completion", "model": b.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": txt}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

async def log(r):
    return web.json_response(LOG)

app = web.Application()
app.add_routes([web.get("/api/tags", tags), web.post("/api/pull", pull), web.post("/api/create", create), web.post("/api/show", show),
                web.get("/v1/models", models), web.post("/v1/chat/completions", chat), web.get("/_log", log)])
if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 11434, print=None)
