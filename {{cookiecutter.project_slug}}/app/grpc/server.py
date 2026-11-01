{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""The gRPC server. Run it as its own process: ``python -m app.grpc.server``."""
import asyncio
import logging
import signal
from pathlib import Path

import grpc
from grpc_reflection.v1alpha import reflection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_sessionmaker
from app.grpc.generated import items_pb2, items_pb2_grpc
{%- if auth %}
from app.grpc.interceptors import AuthInterceptor
{%- endif %}
from app.grpc.servicers import ItemServicer
{%- if realtime %}
from app.realtime import RealtimeState, create_broker
from app.realtime.broker import EventBroker
{%- endif %}

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 5.0


def build_server(
    sessionmaker: async_sessionmaker[AsyncSession],
{%- if realtime %}
    broker: EventBroker,
    realtime: RealtimeState,
{%- endif %}
    environment: str,
) -> grpc.aio.Server:
    """A server with the item service registered (not yet bound to a port)."""
    server = grpc.aio.server(
{%- if auth %}
        interceptors=[AuthInterceptor(sessionmaker)],
{%- endif %}
    )
    items_pb2_grpc.add_ItemServiceServicer_to_server(
        ItemServicer(sessionmaker{% if realtime %}, broker, realtime{% endif %}), server
    )
    if environment == "dev":
        # Reflection lets tools such as grpcurl discover the API: development only.
        service = items_pb2.DESCRIPTOR.services_by_name["ItemService"]
        reflection.enable_server_reflection((service.full_name, reflection.SERVICE_NAME), server)
    return server


async def serve() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
{%- if realtime %}
    realtime = RealtimeState(settings.realtime_max_connections)
{%- endif %}
    server = build_server(
        get_sessionmaker(),
{%- if realtime %}
        create_broker(),
        realtime,
{%- endif %}
        settings.environment,
    )
    address = f"[::]:{settings.grpc_port}"
    if settings.grpc_tls_cert and settings.grpc_tls_key:
        credentials = grpc.ssl_server_credentials(
            [(Path(settings.grpc_tls_key).read_bytes(), Path(settings.grpc_tls_cert).read_bytes())]
        )
        server.add_secure_port(address, credentials)
    else:
        server.add_insecure_port(address)
        if settings.environment != "dev":
            logger.warning(
                "gRPC is serving plaintext: set GRPC_TLS_CERT and GRPC_TLS_KEY, "
                "or terminate TLS in front of it"
            )
    await server.start()
    logger.info("gRPC server listening on %s", address)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    await stop.wait()
{%- if realtime %}
    realtime.closing.set()  # open event streams end by themselves
{%- endif %}
    await server.stop(SHUTDOWN_GRACE_SECONDS)


if __name__ == "__main__":
    asyncio.run(serve())
