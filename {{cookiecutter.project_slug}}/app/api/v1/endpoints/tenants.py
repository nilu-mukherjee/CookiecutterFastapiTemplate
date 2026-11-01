{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set social = cookiecutter.use_social_login == "y" -%}
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import {% if api_key %}HumanPrincipal, {% endif %}SuperuserPrincipal, TenantServiceDep
from app.schemas.v1.tenant import TenantCreate, TenantList, TenantRead, TenantUpdate

# Platform operation, superuser-only. A superuser manages tenants but never reads or
# changes another tenant's items, users or roles. There is no delete: deactivate.
router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    service: TenantServiceDep,
    _: SuperuserPrincipal,
{%- if api_key %}
    # A leaked API key must not be able to create tenants (and their admins).
    __: HumanPrincipal,
{%- endif %}
) -> TenantRead:
    """Create the tenant{% if cookiecutter.use_rbac == "y" %}, its default roles{% endif %} and its first admin in one transaction."""
    tenant = await service.create_with_admin(
        body.name,
        body.slug,
        body.admin_email,
        body.admin_password,
{%- if social %}
        allow_social_signup=body.allow_social_signup,
{%- endif %}
    )
    return TenantRead.model_validate(tenant)


@router.get("", response_model=TenantList)
async def list_tenants(
    service: TenantServiceDep,
    _: SuperuserPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TenantList:
    tenants, total = await service.list(limit, offset)
    return TenantList(
        items=[TenantRead.model_validate(t) for t in tenants],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant(
    tenant_id: UUID, service: TenantServiceDep, _: SuperuserPrincipal
) -> TenantRead:
    return TenantRead.model_validate(await service.get(tenant_id))


@router.patch("/{tenant_id}", response_model=TenantRead)
async def update_tenant(
    tenant_id: UUID, body: TenantUpdate, service: TenantServiceDep, _: SuperuserPrincipal
) -> TenantRead:
    """Rename or (de)activate. Deactivating locks the tenant's users out on their next
    request, whatever the credential; the system tenant cannot be deactivated."""
    tenant = await service.update(tenant_id, body.model_dump(exclude_unset=True))
    return TenantRead.model_validate(tenant)
