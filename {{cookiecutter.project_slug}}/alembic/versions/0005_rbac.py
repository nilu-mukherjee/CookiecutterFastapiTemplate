{%- set previous = "0004" if cookiecutter.use_auth_api_key == "y" else ("0003" if cookiecutter.use_auth_session == "y" else "0002") -%}
"""rbac: roles and permissions

Revision ID: 0005
Revises: {{ previous }}

The default permissions and roles are seeded in code at startup (RbacService), not here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
# The newest revision that exists in this build (0003/0004 are only generated with
# sessions / API keys).
down_revision = "{{ previous }}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_index("ix_permissions_created_at", "permissions", ["created_at"])
    op.create_index("ix_permissions_name", "permissions", ["name"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_index("ix_roles_created_at", "roles", ["created_at"])
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            sa.Uuid(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            sa.Uuid(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])

    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.Uuid(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_roles_name", table_name="roles")
    op.drop_index("ix_roles_created_at", table_name="roles")
    op.drop_table("roles")
    op.drop_index("ix_permissions_name", table_name="permissions")
    op.drop_index("ix_permissions_created_at", table_name="permissions")
    op.drop_table("permissions")
