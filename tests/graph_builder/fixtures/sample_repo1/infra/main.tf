# IaC datastore declarations -> `db_instance` anchors (tier: verified).
# Two managed Postgres instances, one per stateful service.

resource "aws_db_instance" "orders_db" {
  identifier     = "orders-db"
  engine         = "postgres"
  engine_version = "16.3"
  instance_class = "db.t3.medium"
  db_name        = "orders"
}

resource "aws_db_instance" "checkout_db" {
  identifier     = "checkout-db"
  engine         = "postgres"
  engine_version = "16.3"
  instance_class = "db.t3.small"
  db_name        = "checkout"
}
