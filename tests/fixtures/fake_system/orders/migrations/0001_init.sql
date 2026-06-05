-- golang-migrate migration. Presence binds orders -> orders-db (writes_to).
CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL,
    total_cents BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
