"""Meta-tests: make the tenant filter impossible to forget.

1. Every model with a ``tenant_id`` column is only reachable through a
   ``TenantScopedRepository``, and any other class in ``app.repositories`` is either a
   repository of a non-tenant model or explicitly allow-listed below with its reason.
2. Every public query method of every scoped repository is executed against SQLite and
   each SQL statement it issues on its model's table must carry the tenant predicate
   with the repository's tenant id. A new method that builds ``select(Model)`` without
   ``self._scoped(...)`` fails here.
"""
import importlib
import inspect
import pkgutil
import re
import types
import typing
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar, get_args, get_origin
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.repositories
from app.models import Base, Item, Tenant
from app.repositories.base import BaseRepository, TenantScopedRepository
from app.repositories.item import ItemRepository

# Classes in app.repositories that may touch users/tenant data without the tenant filter.
# Adding one is a security decision: say why here.
ALLOWED_UNSCOPED = {
    # The authentication stage: no tenant is known yet, it is derived from the user found.
    "GlobalUserLookup",
}


def repository_classes() -> list[type]:
    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(app.repositories.__path__):
        module = importlib.import_module(f"app.repositories.{info.name}")
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module.__name__:
                found[f"{module.__name__}.{name}"] = obj
    return list(found.values())


def tenant_models() -> list[type]:
    return [
        m.class_ for m in Base.registry.mappers if "tenant_id" in m.local_table.columns
    ]


def scoped_classes() -> list[type[TenantScopedRepository[Any]]]:
    """Concrete scoped repositories (the generic base has no model)."""
    return [
        c
        for c in repository_classes()
        if issubclass(c, TenantScopedRepository) and hasattr(c, "model")
    ]


def test_every_tenant_owned_model_is_only_served_by_a_scoped_repository() -> None:
    owned = tenant_models()
    assert {m.__tablename__ for m in owned} >= {"items", "users"}
    classes = repository_classes()
    for model in owned:
        repos = [c for c in classes if getattr(c, "model", None) is model]
        assert repos, f"{model.__name__} has no repository"
        for repo in repos:
            assert issubclass(repo, TenantScopedRepository), (
                f"{repo.__name__} serves tenant-owned {model.__name__} but is not a "
                "TenantScopedRepository"
            )
    for cls in classes:
        if cls in (BaseRepository, TenantScopedRepository) or cls.__name__ in ALLOWED_UNSCOPED:
            continue
        assert issubclass(cls, BaseRepository), f"{cls.__name__}: unknown repository kind"
        if not issubclass(cls, TenantScopedRepository):
            assert "tenant_id" not in cls.model.__table__.columns, cls.__name__


def test_a_scoped_repository_needs_a_uuid_tenant_id(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    session = maker()
    with pytest.raises(TypeError):
        ItemRepository(session)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ItemRepository(session, "some-tenant")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ItemRepository(session, None)  # type: ignore[arg-type]


async def test_scoped_delete_of_another_tenants_row_is_a_no_op(
    maker: async_sessionmaker[AsyncSession], tenant: Tenant, tenant_b: Tenant
) -> None:
    async with maker() as session:
        theirs = await ItemRepository(session, tenant_b.id).add(Item(name="theirs"))
        await session.commit()
    async with maker() as session:
        await ItemRepository(session, tenant.id).delete(theirs)
        await session.commit()
    async with maker() as session:
        assert await session.scalar(select(Item).where(Item.id == theirs.id)) is not None


# --- 2. every statement of every scoped method carries the tenant filter ---------------


def new_instance(model: type, tenant_id: UUID) -> Any:
    """A transient row of the model, deliberately tagged with ANOTHER tenant."""
    values: dict[str, Any] = {"id": uuid4(), "tenant_id": tenant_id}
    for column in model.__table__.columns:
        if column.name in values or column.nullable or column.default is not None:
            continue
        python_type = column.type.python_type
        values[column.name] = f"{uuid4().hex}@example.com" if python_type is str else None
    return model(**values)


def sample(annotation: Any, model: type, other_tenant: UUID, where: str) -> Any:
    """An argument for a parameter, from its type. Unknown types fail loudly: extend this."""
    if isinstance(annotation, TypeVar) or annotation is model:
        return new_instance(model, other_tenant)
    if annotation is UUID:
        return uuid4()
    if annotation is int:
        return 5
    if annotation is str:
        return "sample"
    if annotation is datetime:
        return datetime(2024, 1, 1, tzinfo=UTC)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return sample(next(a for a in args if a is not type(None)), model, other_tenant, where)
    if origin in (Sequence, list):
        return [sample(args[0], model, other_tenant, where)]
    if origin is tuple:
        return tuple(sample(a, model, other_tenant, where) for a in args)
    if inspect.isclass(annotation) and issubclass(annotation, Base):
        return new_instance(annotation, other_tenant)
    raise AssertionError(f"no sample value for {where}: {annotation!r}; extend sample()")


def public_query_methods(cls: type) -> Iterator[tuple[str, Any]]:
    for name, fn in inspect.getmembers(cls, inspect.iscoroutinefunction):
        if not name.startswith("_"):
            yield name, fn


SCOPED_METHODS = [
    pytest.param(cls, name, id=f"{cls.__name__}.{name}")
    for cls in scoped_classes()
    for name, _ in public_query_methods(cls)
]


@pytest.mark.parametrize(("cls", "method_name"), SCOPED_METHODS)
async def test_every_scoped_query_carries_the_tenant_predicate(
    maker: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    tenant_b: Tenant,
    cls: type[TenantScopedRepository[Any]],
    method_name: str,
) -> None:
    engine = maker.kw["bind"]
    table = cls.model.__tablename__
    statements: list[tuple[str, str]] = []

    def capture(conn: Any, cursor: Any, sql: str, params: Any, context: Any, many: bool) -> None:
        statements.append((sql.lower(), repr(params)))

    method = getattr(cls, method_name)
    hints = typing.get_type_hints(method)
    async with maker() as session:
        repo = cls(session, tenant.id)
        where = f"{cls.__name__}.{method_name}"
        kwargs = {
            p: sample(hints.get(p, Any), cls.model, tenant_b.id, f"{where}({p})")
            for p in list(inspect.signature(method).parameters)[1:]
        }
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        try:
            await getattr(repo, method_name)(**kwargs)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
            await session.rollback()

    on_table = [(sql, p) for sql, p in statements if re.search(rf"\b{table}\b", sql)]
    assert on_table, f"{cls.__name__}.{method_name} issued no statement on {table}"
    for sql, params in on_table:
        assert "tenant_id" in sql, f"{cls.__name__}.{method_name} without tenant filter: {sql}"
        assert tenant.id.hex in params or str(tenant.id) in params, (
            f"{cls.__name__}.{method_name} is not bound to the repository's tenant: {sql} {params}"
        )
        assert tenant_b.id.hex not in params and str(tenant_b.id) not in params, (
            f"{cls.__name__}.{method_name} used a caller-supplied tenant: {sql} {params}"
        )
