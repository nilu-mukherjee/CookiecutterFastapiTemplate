{%- set use_jwt = (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") -%}
"""auth: users{% if use_jwt %} and refresh tokens{% endif %}

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)
{%- if use_jwt %}
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_created_at", "refresh_tokens", ["created_at"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
{%- endif %}


def downgrade() -> None:
{%- if use_jwt %}
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_created_at", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
{%- endif %}
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_table("users")
