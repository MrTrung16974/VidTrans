from starlette.staticfiles import StaticFiles


class FrontendStaticFiles(StaticFiles):
    """Reject unmatched WebSockets instead of passing them to HTTP StaticFiles."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)
