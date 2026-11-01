{%- set previous = "0005" if cookiecutter.use_rbac == "y" else ("0004" if cookiecutter.use_auth_api_key == "y" else ("0003" if cookiecutter.use_auth_session == "y" else "0002")) -%}
"""social login: linked provider accounts

Revision ID: 0006
Revises: {{ previous }}
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
# The newest revision that exists in this build (0003/0004/0005 are only generated with
# sessions / API keys / RBAC).
down_revision = "{{ previous }}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        # A provider account links to one user; a user has one account per provider.
        sa.UniqueConstraint("provider", "subject", name="uq_social_accounts_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_social_accounts_user_id_provider"),
    )
    op.create_index("ix_social_accounts_created_at", "social_accounts", ["created_at"])
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_social_accounts_user_id", table_name="social_accounts")
    op.drop_index("ix_social_accounts_created_at", table_name="social_accounts")
    op.drop_table("social_accounts")
