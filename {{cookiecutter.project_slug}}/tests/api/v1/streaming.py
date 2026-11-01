"""Incremental reads of a streaming response.

httpx's ASGI transport returns only once the app has finished, which an event stream
never does, so these tests drive the ASGI app directly. Every wait is bounded: a hung
stream fails the test instead of hanging it.
"""
import asyncio
from types import TracebackType
from typing import Self

from fastapi import FastAPI
from httpx import AsyncClient
from starlette.types import Message, Scope

TIMEOUT = 2.0


class Stream:
    """One GET request against ``app``, with the credentials ``client`` would send."""

    def __init__(
        self,
        app: FastAPI,
        client: AsyncClient,
        path: str,
        headers: dict[str, str] | None = None,
        spec_version: str = "2.3",
    ) -> None:
        request = client.build_request("GET", path, headers=headers)
        self._scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "root_path": "",
            "headers": [(k.lower(), v) for k, v in request.headers.raw],  # ASGI: lowercase
            "server": ("test", 80),
            "client": ("127.0.0.1", 50000),
        }
        self._app = app
        self._messages: asyncio.Queue[Message] = asyncio.Queue()
        self._disconnected = asyncio.Event()
        self._request_sent = False
        self._buffer = ""
        self._finished = False
        self.status = 0
        self.headers: dict[str, str] = {}
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None

    async def _receive(self) -> Message:
        if not self._request_sent:
            self._request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnected.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: Message) -> None:
        self._messages.put_nowait(message)

    async def _run(self) -> None:
        try:
            await self._app(self._scope, self._receive, self._send)
        except Exception as exc:
            self.error = exc
        finally:
            self._messages.put_nowait({"type": "done"})

    async def __aenter__(self) -> Self:
        self.task = asyncio.create_task(self._run())
        message = await asyncio.wait_for(self._messages.get(), TIMEOUT)
        assert message["type"] == "http.response.start", message
        self.status = message["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def next_frame(self, timeout: float = TIMEOUT) -> str | None:
        """The next ``\\n\\n``-terminated frame, or None when the stream has ended.

        Raises TimeoutError when nothing arrives within ``timeout``.
        """
        while "\n\n" not in self._buffer:
            if self._finished:
                return None
            message = await asyncio.wait_for(self._messages.get(), timeout)
            if message["type"] == "http.response.body":
                self._buffer += message.get("body", b"").decode()
            elif message["type"] == "done":
                self._finished = True
        frame, _, self._buffer = self._buffer.partition("\n\n")
        return frame + "\n\n"

    async def body(self) -> bytes:
        """Everything the response sends until it ends (for finite responses)."""
        out = self._buffer.encode()
        self._buffer = ""
        while not self._finished:
            message = await asyncio.wait_for(self._messages.get(), TIMEOUT)
            if message["type"] == "http.response.body":
                out += message.get("body", b"")
            elif message["type"] == "done":
                self._finished = True
        return out

    async def close(self) -> None:
        """Disconnect the client and wait for the app to finish; it must not hang or fail."""
        self._disconnected.set()
        assert self.task is not None
        await asyncio.wait_for(self.task, TIMEOUT)
        assert self.error is None, repr(self.error)

    async def ended(self) -> None:
        """Wait for the server to end the stream by itself (no disconnect)."""
        assert self.task is not None
        await asyncio.wait_for(asyncio.shield(self.task), TIMEOUT)
        assert self.error is None, repr(self.error)
