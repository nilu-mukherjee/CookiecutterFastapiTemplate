{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
{%- set social = tenancy and cookiecutter.use_social_login == "y" -%}
"""create {% if tenancy %}tenants and {% endif %}items

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
{%- if tenancy %}
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
{%- if social %}
        sa.Column("allow_social_signup", sa.Boolean(), nullable=False),
{%- endif %}
    )
    op.create_index("ix_tenants_created_at", "tenants", ["created_at"])
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
{%- endif %}
    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
{%- if tenancy %}
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
{%- endif %}
    )
    op.create_index("ix_items_created_at", "items", ["created_at"])
{%- if tenancy %}
    op.create_index("ix_items_tenant_id", "items", ["tenant_id"])
{%- endif %}


def downgrade() -> None:
{%- if tenancy %}
    op.drop_index("ix_items_tenant_id", table_name="items")
{%- endif %}
    op.drop_index("ix_items_created_at", table_name="items")
    op.drop_table("items")
{%- if tenancy %}
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_index("ix_tenants_created_at", table_name="tenants")
    op.drop_table("tenants")
{%- endif %}
