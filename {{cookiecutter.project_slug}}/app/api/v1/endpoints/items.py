{%- set rbac = cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
{%- set celery = cookiecutter.use_celery == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
{% if celery %}import logging
{% endif %}from typing import Annotated
from uuid import UUID

{% if cookiecutter.use_auth == "y" %}from fastapi import APIRouter, Depends, Query, Response
{% else %}from fastapi import APIRouter, Query, Response
{% endif %}{% if celery %}from kombu.exceptions import OperationalError
{% if redis %}from redis.exceptions import RedisError
{% endif %}from starlette.concurrency import run_in_threadpool
{% endif %}
{% if cookiecutter.use_auth == "y" %}from app.api.deps import ItemServiceDep, {% if tenancy and celery %}TenantId, {% endif %}get_current_principal{% if rbac %}, require_permission{% endif %}
{% else %}from app.api.deps import ItemServiceDep
{% endif %}{% if celery %}from app.core.exceptions import AppError
{% endif %}from app.schemas.v1.item import ItemCreate, ItemList, ItemRead, ItemUpdate{% if celery %}, TaskAccepted
from app.worker.tasks.example import example_task

logger = logging.getLogger(__name__)
{% else %}
{% endif %}
{% if cookiecutter.use_auth == "y" %}router = APIRouter(
    prefix="/items",
    tags=["items"],
    dependencies=[Depends(get_current_principal)],
)
{% else %}router = APIRouter(prefix="/items", tags=["items"])
{% endif %}{% if rbac %}
# Checked after authentication, so an unauthenticated request is a 401, not a 403.
READ = [Depends(require_permission("items:read"))]
WRITE = [Depends(require_permission("items:write"))]
{% endif %}

@router.post("", response_model=ItemRead, status_code=201{% if rbac %}, dependencies=WRITE{% endif %})
async def create_item(body: ItemCreate, service: ItemServiceDep) -> ItemRead:
    return ItemRead.model_validate(await service.create(body.name, body.description))


@router.get("", response_model=ItemList{% if rbac %}, dependencies=READ{% endif %})
async def list_items(
    service: ItemServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemList:
    items, total = await service.list(limit, offset)
    return ItemList(
        items=[ItemRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{item_id}", response_model=ItemRead{% if rbac %}, dependencies=READ{% endif %})
async def get_item(item_id: UUID, service: ItemServiceDep) -> ItemRead:
    return ItemRead.model_validate(await service.get(item_id))


@router.patch("/{item_id}", response_model=ItemRead{% if rbac %}, dependencies=WRITE{% endif %})
async def update_item(item_id: UUID, body: ItemUpdate, service: ItemServiceDep) -> ItemRead:
    item = await service.update(item_id, body.model_dump(exclude_unset=True))
    return ItemRead.model_validate(item)


@router.delete("/{item_id}", status_code=204{% if rbac %}, dependencies=WRITE{% endif %})
async def delete_item(item_id: UUID, service: ItemServiceDep) -> Response:
    await service.delete(item_id)
    return Response(status_code=204)
{%- if celery %}


@router.post(
    "/{item_id}/process",
    response_model=TaskAccepted,
    status_code=202{% if rbac %},
    dependencies=WRITE{% endif %},
)
async def process_item(
    item_id: UUID,
    service: ItemServiceDep,
{%- if tenancy %}
    tenant_id: TenantId,
{%- endif %}
) -> TaskAccepted:
    """Queue background processing of an item the caller can see (404 otherwise)."""
    await service.get(item_id)
    try:
        # Blocking publish: keep it off the event loop. retry=False fails fast when the
        # broker is down. Arguments are explicit and JSON-only: ids, never contents.
        result = await run_in_threadpool(
            example_task.apply_async,
            kwargs={
{%- if tenancy %}
                "tenant_id": str(tenant_id),
{%- endif %}
                "payload": {"item_id": str(item_id)},
            },
            retry=False,
        )
    except (OperationalError, OSError{% if redis %}, RedisError{% endif %}) as exc:
        # Log the class only: the message can contain the broker URL and credentials.
        logger.warning("task queue unavailable: %s", type(exc).__name__)
        raise AppError(503, "queue_unavailable", "Task queue is unavailable") from exc
    return TaskAccepted(task_id=result.id)
{%- endif %}
