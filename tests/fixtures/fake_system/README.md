# `fake_system` — L0 survey golden fixture

A tiny, deliberately **unambiguous** polyglot + k8s system. It is the hand-authored
answer key for the L0 survey/grounding agent: point the scanner tools at this tree
and the output must equal `expected/survey_snapshot.json`.

Design rule: **every row of the L0 detection table (`docs/l0-survey-agent.md` §3) has
exactly one file that is its evidence.** No row is untested; no signal is duplicated
across two sources (that messiness is what the *vendored real demo* is for — see
`scripts/vendor_demo_system.ps1`).

## The system

```
checkout (py) ──calls──▶ auth-service (node)
   │   │   └──calls──▶ orders (go) ──writes_to──▶ orders-db   (Terraform RDS)
   │   ├──writes_to──▶ checkout-db  (Terraform RDS, Alembic)
   │   ├──writes_to──▶ order-events (Kafka topic)
   │   └──depends_on─▶ payments     (INFERRED — code string only)
libs/common-utils (py)  ── library, NOT a service (negative case)
```

## Detection-table coverage

| Spec §3 signal | Evidence file | Expected anchor / edge | Tier |
|---|---|---|---|
| Deploy manifest (k8s Deployment + Service) | `*/k8s/deployment.yaml` | `k8s_workload`, `service_identity` ×3 | verified |
| Dockerfile + build context | `*/Dockerfile` | `path` ↔ image bind ×3 | verified |
| Build descriptor + entrypoint (server vs lib) | `auth-service/package.json`, `checkout/pyproject.toml`, `orders/go.mod` | `repo`, `path` ×3 | strong |
| IaC datastore decl (Terraform) | `infra/main.tf` | `db_instance` ×2 (orders-db, checkout-db) | verified |
| DB binding in code (env + migrations) | `checkout/alembic/…`, `orders/migrations/…`, `DATABASE_URL` env | `writes_to` ×2 | strong |
| Cross-service ref (Service DNS in env) | `checkout/k8s/deployment.yaml` env | `calls` ×2 (→auth, →orders) | strong |
| Queue/topic ref (Kafka) | `checkout/k8s/deployment.yaml` `KAFKA_TOPIC` | `writes_to` order-events | strong |
| Naming/heuristic only | `checkout/app/main.py` `PAYMENTS_URL` | `payments` node + `depends_on`, **flagged** | inferred |

## The two assertions that catch the hard bugs

1. **Negative — no phantom service.** `libs/common-utils` has no entrypoint, no
   Dockerfile, no workload. It must produce **no service component**. A `common-utils`
   node in the output means the service-vs-lib parser is broken.
2. **Inferred stays flagged.** `payments` exists only as a hardcoded string in code.
   It must appear with `confidence_tier: "inferred"` and must **never** be eligible for
   click-into-truth. Promoting it to `strong`/`verified` is a grounding failure.

## Using it in a test

```python
import json, pathlib

FIX = pathlib.Path(__file__).parent / "fixtures" / "fake_system"
expected = json.loads((FIX / "expected" / "survey_snapshot.json").read_text())

snap = run_survey_agent(FIX)          # your L0 entrypoint

# Normalize before diffing: drop `scanned_at` and the :line suffix of evidence,
# then compare components/edges as sets keyed on (id/type/value, tier).
assert normalize(snap) == normalize(expected)
```

`scanned_at` and the `:line` suffix of each `evidence` are **advisory** — normalize
them out before comparing so the golden file isn't brittle to formatting changes.
