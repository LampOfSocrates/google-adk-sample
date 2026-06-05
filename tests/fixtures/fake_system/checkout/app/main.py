"""checkout service entrypoint.

Cross-service / datastore references in this module and in k8s/deployment.yaml are
the evidence L0 turns into edges:

  - AUTH_SERVICE_URL  -> calls auth-service      (from env, k8s)        [strong]
  - ORDERS_URL        -> calls orders            (from env, k8s)        [strong]
  - DATABASE_URL      -> writes_to checkout-db   (env + alembic/)       [strong]
  - KAFKA_TOPIC       -> writes_to order-events  (env, k8s)             [strong]
  - PAYMENTS_URL      -> depends_on payments     (HARDCODED here only)  [inferred]

The PAYMENTS_URL line is deliberately NOT in any manifest. There is no payments
workload, no Service, no build descriptor — only this string. So `payments` must
surface as an `inferred` component and the edge must be flagged, never used for
click-into-truth.
"""
import os

AUTH_SERVICE_URL = os.environ["AUTH_SERVICE_URL"]
ORDERS_URL = os.environ["ORDERS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "order-events")

# Inferred-only dependency: hardcoded base URL, no config/manifest backing it.
PAYMENTS_URL = "http://payments.internal:7000"


def main() -> None:
    print(f"checkout starting; auth={AUTH_SERVICE_URL} orders={ORDERS_URL}")


if __name__ == "__main__":
    main()
