{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""The gRPC item service: the same operations, auth, permissions and tenancy as REST."""
{% if realtime %}import asyncio
{% endif %}import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import grpc
import pytest
import pytest_asyncio
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.grpc.generated import items_pb2, items_pb2_grpc
from app.grpc.server import build_server
{%- if auth %}
from app.models import User
{%- endif %}
{%- if realtime %}
from app.realtime import RealtimeState, create_broker
{%- endif %}
{%- if auth %}
from tests.conftest import {% if api_key %}MakeApiKey, {% endif %}MakeUser
{%- endif %}

ROOT = Path(__file__).resolve().parents[2]
Metadata = tuple[tuple[str, str], ...]
{%- if auth %}
Credentials = Callable[[User], Awaitable[Metadata]]
{%- endif %}


{%- if realtime %}


@pytest_asyncio.fixture
async def realtime_state() -> RealtimeState:
    return RealtimeState(1000)
{%- endif %}


@pytest_asyncio.fixture
async def start_server(
    maker: async_sessionmaker[AsyncSession],
{%- if realtime %}
    realtime_state: RealtimeState,
{%- endif %}
) -> AsyncIterator[Callable[[str], Awaitable[str]]]:
    """Starts an in-process server on an ephemeral port and returns its address."""
    servers: list[grpc.aio.Server] = []
{%- if realtime %}
    broker = create_broker()
{%- endif %}

    async def _start(environment: str) -> str:
        server = build_server(
            maker,
{%- if realtime %}
            broker,
            realtime_state,
{%- endif %}
            environment,
        )
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        servers.append(server)
        return f"127.0.0.1:{port}"

    yield _start
    for server in servers:
        await server.stop(0)


@pytest_asyncio.fixture
async def stub(
    start_server: Callable[[str], Awaitable[str]],
) -> AsyncIterator[items_pb2_grpc.ItemServiceStub]:
    async with grpc.aio.insecure_channel(await start_server("dev")) as channel:
        yield items_pb2_grpc.ItemServiceStub(channel)
{%- if auth %}


@pytest.fixture
def credentials(
{%- if bearer %}
    auth_headers: Callable[[User], dict[str, str]],
{%- else %}
    make_api_key: MakeApiKey,
{%- endif %}
) -> Credentials:
    """Call metadata that authenticates as the user (Bearer, else an API key)."""

    async def _credentials(user: User) -> Metadata:
{%- if bearer %}
        return (("authorization", auth_headers(user)["Authorization"]),)
{%- else %}
        return (("x-api-key", await make_api_key(user)),)
{%- endif %}

    return _credentials


@pytest_asyncio.fixture
async def member(make_user: MakeUser, credentials: Credentials) -> Metadata:
    return await credentials(await make_user("member@example.com"))
{%- else %}


@pytest.fixture
def member() -> Metadata:
    return ()
{%- endif %}


async def status_of(call: Any) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await call
    code: grpc.StatusCode = raised.value.code()
    return code


async def create(
    stub: items_pb2_grpc.ItemServiceStub, metadata: Metadata, name: str = "a"
) -> items_pb2.Item:
    return await stub.CreateItem(items_pb2.CreateItemRequest(name=name), metadata=metadata)


async def test_crud_roundtrip(stub: items_pb2_grpc.ItemServiceStub, member: Metadata) -> None:
    created = await stub.CreateItem(
        items_pb2.CreateItemRequest(name="n", description="d"), metadata=member
    )
    assert (created.name, created.description) == ("n", "d")
    assert created.HasField("description")
    assert created.created_at.seconds > 0

    fetched = await stub.GetItem(items_pb2.GetItemRequest(id=created.id), metadata=member)
    assert fetched == created

    renamed = await stub.UpdateItem(
        items_pb2.UpdateItemRequest(id=created.id, name="m"), metadata=member
    )
    assert (renamed.name, renamed.description) == ("m", "d")  # unset field: unchanged

    cleared = await stub.UpdateItem(
        items_pb2.UpdateItemRequest(id=created.id, description=""), metadata=member
    )
    assert cleared.name == "m"
    assert not cleared.HasField("description")

    listed = await stub.ListItems(items_pb2.ListItemsRequest(), metadata=member)
    assert (listed.total, listed.limit, listed.offset) == (1, 20, 0)
    assert [i.id for i in listed.items] == [created.id]

    await stub.DeleteItem(items_pb2.DeleteItemRequest(id=created.id), metadata=member)
    assert (
        await status_of(stub.GetItem(items_pb2.GetItemRequest(id=created.id), metadata=member))
        == grpc.StatusCode.NOT_FOUND
    )


async def test_list_pages(stub: items_pb2_grpc.ItemServiceStub, member: Metadata) -> None:
    for name in ("one", "two", "three"):
        await create(stub, member, name)

    page = await stub.ListItems(items_pb2.ListItemsRequest(limit=2, offset=1), metadata=member)

    assert (page.total, page.limit, page.offset, len(page.items)) == (3, 2, 1, 2)


async def test_not_found_maps_to_not_found(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata
) -> None:
    ghost = "00000000-0000-0000-0000-000000000000"
    calls = [
        stub.GetItem(items_pb2.GetItemRequest(id=ghost), metadata=member),
        stub.UpdateItem(items_pb2.UpdateItemRequest(id=ghost, name="x"), metadata=member),
        stub.DeleteItem(items_pb2.DeleteItemRequest(id=ghost), metadata=member),
    ]
    for call in calls:
        assert await status_of(call) == grpc.StatusCode.NOT_FOUND


async def test_invalid_argument_maps(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata
) -> None:
    item = await create(stub, member)
    calls = [
        stub.CreateItem(items_pb2.CreateItemRequest(name=""), metadata=member),
        stub.GetItem(items_pb2.GetItemRequest(id="not-a-uuid"), metadata=member),
        stub.UpdateItem(items_pb2.UpdateItemRequest(id=item.id, name=""), metadata=member),
        stub.ListItems(items_pb2.ListItemsRequest(limit=101), metadata=member),
        stub.ListItems(items_pb2.ListItemsRequest(offset=-1), metadata=member),
    ]
    for call in calls:
        assert await status_of(call) == grpc.StatusCode.INVALID_ARGUMENT


async def test_domain_errors_carry_the_message(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await stub.GetItem(
            items_pb2.GetItemRequest(id="00000000-0000-0000-0000-000000000000"), metadata=member
        )
    assert raised.value.details() == "Item not found"
{%- if auth %}


@pytest.mark.parametrize(
    "metadata",
    [
        (),
        (("authorization", "Bearer not-a-token"),),
{%- if api_key %}
        (("x-api-key", "not-a-key"),),
{%- endif %}
        (("authorization", "Basic dXNlckBleGFtcGxlLmNvbTpwdw=="),),
        (("cookie", "a=b; c=d"),),
    ],
)
async def test_unauthenticated_call_gets_unauthenticated(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata, metadata: Metadata
) -> None:
    request = items_pb2.ListItemsRequest()
    assert await status_of(stub.ListItems(request, metadata=metadata)) == (
        grpc.StatusCode.UNAUTHENTICATED
    )
    assert (await stub.ListItems(request, metadata=member)).total == 0


async def test_unauthenticated_write_changes_nothing(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata
) -> None:
    request = items_pb2.CreateItemRequest(name="x")
    assert await status_of(stub.CreateItem(request)) == grpc.StatusCode.UNAUTHENTICATED
    assert (await stub.ListItems(items_pb2.ListItemsRequest(), metadata=member)).total == 0
{%- endif %}
{%- if api_key %}


async def test_api_key_metadata_accepted(
    stub: items_pb2_grpc.ItemServiceStub, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    key = await make_api_key(await make_user("keyed@example.com"))
    created = await create(stub, (("x-api-key", key),))
    assert created.name == "a"
{%- endif %}
{%- if rbac %}


async def test_missing_permission_gets_permission_denied(
    stub: items_pb2_grpc.ItemServiceStub,
    make_user: MakeUser,
    credentials: Credentials,
    member: Metadata,
) -> None:
    item = await create(stub, member)
    nobody = await credentials(await make_user("nobody@example.com", roles=[]))
    calls = [
        stub.ListItems(items_pb2.ListItemsRequest(), metadata=nobody),
        stub.GetItem(items_pb2.GetItemRequest(id=item.id), metadata=nobody),
        stub.CreateItem(items_pb2.CreateItemRequest(name="x"), metadata=nobody),
        stub.DeleteItem(items_pb2.DeleteItemRequest(id=item.id), metadata=nobody),
    ]
    for call in calls:
        assert await status_of(call) == grpc.StatusCode.PERMISSION_DENIED
    assert (await stub.ListItems(items_pb2.ListItemsRequest(), metadata=member)).total == 1
{%- endif %}
{%- if tenancy %}


@pytest_asyncio.fixture
async def member_b(make_user: MakeUser, credentials: Credentials, tenant_b: Any) -> Metadata:
    return await credentials(await make_user("member-b@example.com", tenant=tenant_b))


async def test_cross_tenant_get_is_not_found(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata, member_b: Metadata
) -> None:
    b_item = await create(stub, member_b, "b-secret")

    calls = [
        stub.GetItem(items_pb2.GetItemRequest(id=b_item.id), metadata=member),
        stub.UpdateItem(items_pb2.UpdateItemRequest(id=b_item.id, name="x"), metadata=member),
        stub.DeleteItem(items_pb2.DeleteItemRequest(id=b_item.id), metadata=member),
    ]
    for call in calls:
        assert await status_of(call) == grpc.StatusCode.NOT_FOUND
    assert (await stub.ListItems(items_pb2.ListItemsRequest(), metadata=member)).total == 0
    assert (await stub.GetItem(items_pb2.GetItemRequest(id=b_item.id), metadata=member_b)).name == (
        "b-secret"
    )
{%- endif %}
{%- if realtime %}


async def subscribe(
    stub: items_pb2_grpc.ItemServiceStub, realtime_state: RealtimeState, metadata: Metadata
) -> Any:
    """Open the event stream and wait until the server is following the channel."""
    before = realtime_state.active
    call = stub.StreamItemEvents(items_pb2.StreamItemEventsRequest(), metadata=metadata)
    for _ in range(100):
        if realtime_state.active > before:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)  # the cursor is taken right after the slot
    return call


async def test_stream_item_events(
    stub: items_pb2_grpc.ItemServiceStub, realtime_state: RealtimeState, member: Metadata
) -> None:
    call = await subscribe(stub, realtime_state, member)
    try:
        item = await create(stub, member)
        await stub.UpdateItem(items_pb2.UpdateItemRequest(id=item.id, name="b"), metadata=member)

        events = [await asyncio.wait_for(call.read(), 2) for _ in range(2)]
    finally:
        call.cancel()

    assert [e.type for e in events] == ["item.created", "item.updated"]
    assert {e.item_id for e in events} == {item.id}


async def test_cancelling_a_stream_releases_its_slot(
    stub: items_pb2_grpc.ItemServiceStub, realtime_state: RealtimeState, member: Metadata
) -> None:
    call = await subscribe(stub, realtime_state, member)
    assert realtime_state.active == 1

    call.cancel()
    for _ in range(100):
        if realtime_state.active == 0:
            break
        await asyncio.sleep(0.02)

    assert realtime_state.active == 0


async def test_stream_refuses_when_full(
    stub: items_pb2_grpc.ItemServiceStub, realtime_state: RealtimeState, member: Metadata
) -> None:
    realtime_state.max_connections = 0  # no free slot
    call = stub.StreamItemEvents(items_pb2.StreamItemEventsRequest(), metadata=member)
    assert await status_of(call.read()) == grpc.StatusCode.UNAVAILABLE
{%- if auth %}


async def test_stream_requires_credentials(stub: items_pb2_grpc.ItemServiceStub) -> None:
    call = stub.StreamItemEvents(items_pb2.StreamItemEventsRequest())
    assert await status_of(call.read()) == grpc.StatusCode.UNAUTHENTICATED
{%- endif %}
{%- if rbac %}


async def test_stream_without_items_read_is_denied(
    stub: items_pb2_grpc.ItemServiceStub, make_user: MakeUser, credentials: Credentials
) -> None:
    nobody = await credentials(await make_user("nobody@example.com", roles=[]))
    call = stub.StreamItemEvents(items_pb2.StreamItemEventsRequest(), metadata=nobody)
    assert await status_of(call.read()) == grpc.StatusCode.PERMISSION_DENIED
{%- endif %}
{%- if tenancy %}


async def test_stream_never_delivers_other_tenant_events(
    stub: items_pb2_grpc.ItemServiceStub,
    realtime_state: RealtimeState,
    member: Metadata,
    member_b: Metadata,
) -> None:
    call_a = await subscribe(stub, realtime_state, member)
    call_b = await subscribe(stub, realtime_state, member_b)
    try:
        a_item = await create(stub, member, "a-secret")

        event = await asyncio.wait_for(call_a.read(), 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(call_b.read(), 0.3)
    finally:
        call_a.cancel()
        call_b.cancel()

    assert event.item_id == a_item.id
{%- endif %}
{%- else %}


async def test_stream_is_unimplemented_without_a_broker(
    stub: items_pb2_grpc.ItemServiceStub, member: Metadata
) -> None:
    call = stub.StreamItemEvents(items_pb2.StreamItemEventsRequest(), metadata=member)
    assert await status_of(call.read()) == grpc.StatusCode.UNIMPLEMENTED
{%- endif %}


async def reflection_services(address: str) -> list[str]:
    async with grpc.aio.insecure_channel(address) as channel:
        reflection = reflection_pb2_grpc.ServerReflectionStub(channel)
        call = reflection.ServerReflectionInfo(
            iter([reflection_pb2.ServerReflectionRequest(list_services="")])
        )
        response = await call.read()
        return [s.name for s in response.list_services_response.service]


async def test_reflection_only_in_dev(start_server: Callable[[str], Awaitable[str]]) -> None:
    assert "items.v1.ItemService" in await reflection_services(await start_server("dev"))

    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await reflection_services(await start_server("prod"))
    assert raised.value.code() == grpc.StatusCode.UNIMPLEMENTED


def test_generated_stubs_match_proto(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("gen_proto", ROOT / "scripts" / "gen_proto.py")
    assert spec is not None and spec.loader is not None
    gen_proto = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_proto)

    gen_proto.generate(tmp_path)

    committed = ROOT / "app" / "grpc" / "generated"
    names = ["items_pb2.py", "items_pb2.pyi", "items_pb2_grpc.py"]
    for name in names:
        assert (tmp_path / name).read_text() == (committed / name).read_text(), (
            f"{name} is stale: run python scripts/gen_proto.py"
        )
