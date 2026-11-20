"""Template for a MODI feature module.  Copy to <id>.py (id must be one of the catalog ids or any new name).
Ignored by MODI because the file name starts with an underscore."""
META = {"title": "Example feature", "description": "What it does, one line.", "requirements": [], "ui": False}

_task = None

async def start(ctx):
    import asyncio
    async def loop():
        while True:
            await asyncio.sleep(3600)
            ctx.notify("hourly ping from the example feature")
    global _task
    _task = asyncio.create_task(loop())

async def stop(ctx):
    if _task:
        _task.cancel()

async def handle(ctx, request):          # optional: GET/POST /api/m/<id>/...
    from aiohttp import web
    return web.json_response({"ok": True, "settings": ctx.settings})
