"""A WebSocket client that talks to the ASGI app in the test's own event loop.

Starlette's TestClient runs the app in another thread and loop, which cannot share the
tests' async database engine. Like ``streaming.Stream``, this drives the app directly,
and every wait is bounded so a hung socket fails the test instead of hanging it.
"""
import asyncio
import json
from types import TracebackType
from typing import Any, Self

from fastapi import FastAPI
from httpx import AsyncClient
from starlette.types import Message, Scope

TIMEOUT = 2.0


class Socket:
    """One WebSocket handshake against ``app``, with the credentials ``client`` would send."""

    def __init__(
        self,
        app: FastAPI,
        client: AsyncClient,
        path: str,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
    ) -> None:
        request = client.build_request("GET", path, headers=headers)
        self._scope: Scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "root_path": "",
            "headers": [(k.lower(), v) for k, v in request.headers.raw],  # ASGI: lowercase
            "server": ("test", 80),
            "client": ("127.0.0.1", 50000),
            "subprotocols": subprotocols or [],
        }
        self._app = app
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._to_app.put_nowait({"type": "websocket.connect"})
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self.accepted = False
        # Set when the server closes: the handshake rejection or the end of the stream.
        self.close_code: int | None = None
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None

    async def _receive(self) -> Message:
        return await self._to_app.get()

    async def _send(self, message: Message) -> None:
        self._from_app.put_nowait(message)

    async def _run(self) -> None:
        try:
            await self._app(self._scope, self._receive, self._send)
        except Exception as exc:
            self.error = exc
        finally:
            self._from_app.put_nowait({"type": "done"})

    async def __aenter__(self) -> Self:
        self.task = asyncio.create_task(self._run())
        message = await asyncio.wait_for(self._from_app.get(), TIMEOUT)
        if message["type"] == "websocket.accept":
            self.accepted = True
        else:
            assert message["type"] == "websocket.close", message
            self.close_code = message["code"]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def send_text(self, text: str) -> None:
        self._to_app.put_nowait({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        self._to_app.put_nowait({"type": "websocket.receive", "bytes": data})

    async def receive_text(self, timeout: float = TIMEOUT) -> str | None:
        """The next text message, or None once the server has closed the socket.

        Raises TimeoutError when nothing arrives within ``timeout``.
        """
        message = await asyncio.wait_for(self._from_app.get(), timeout)
        if message["type"] == "websocket.send":
            return str(message["text"])
        if message["type"] == "websocket.close":
            self.close_code = message["code"]
        return None

    async def receive_json(self, timeout: float = TIMEOUT) -> Any:
        text = await self.receive_text(timeout)
        assert text is not None, f"socket closed with {self.close_code}"
        return json.loads(text)

    async def close(self) -> None:
        """Disconnect the client and wait for the app to finish; it must not hang or fail."""
        self._to_app.put_nowait({"type": "websocket.disconnect", "code": 1000})
        assert self.task is not None
        await asyncio.wait_for(self.task, TIMEOUT)
        assert self.error is None, repr(self.error)

    async def ended(self) -> None:
        """Wait for the server to end the socket by itself (no client disconnect)."""
        assert self.task is not None
        await asyncio.wait_for(asyncio.shield(self.task), TIMEOUT)
        assert self.error is None, repr(self.error)
