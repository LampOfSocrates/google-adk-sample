"""Alembic migration: presence of alembic/versions/ is the ORM/migration evidence
that binds checkout -> checkout-db (writes_to)."""
revision = "0001"
down_revision = None


def upgrade() -> None:
    pass  # CREATE TABLE orders_cart (...) in a real migration


def downgrade() -> None:
    pass
