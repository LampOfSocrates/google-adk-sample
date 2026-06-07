# L0 — Survey / Grounding Agent (spec)

> **Status (2026-06-05): SPEC + FIXTURE ONLY — not yet built.** The golden fixture
> (`tests/fixtures/fake_system/`, with `expected/survey_snapshot.json`) and the Online
> Boutique vendor script (`scripts/vendor_demo_system.ps1`) are ready, but no survey
> agent, scanner tools, or test consuming them exists. Deferred behind the resolver-first
> pivot — `graph_builder` built L1 (resolution) first with grounding faked. See README
> DecisionLog (2026-06-05).

> **Job:** scan repos + deployments and emit a **hard-anchored skeleton** of the system —
> components bound to their real code / workload / datastore, plus the edges that are
> *provable* from config. L0 emits **only anchors, never claims.** It is the objective
> floor everything else attaches to.

Settled scope: **own team / single-tenant** (we control the inputs), **standalone**.

---

## 1. Hard boundary: what L0 does and does not do

| L0 **does** | L0 **does NOT** (deferred) |
|---|---|
| Discover components from code + deploy config | Read prose (wiki/email/incidents) → that's **L2** |
| Bind component → repo/dir, workload, image, DB | Resolve aliases across *prose* sources → **L1** |
| Emit edges provable from config (calls/reads/writes) | Infer purpose/ownership from text → **L2** |
| Attach **anchors** with evidence + confidence | Assign final canonical node id → **L1** |
| Re-scan incrementally; flag drift (anchor vanished) | DB schema/sample-data drill-in → **L4 connector** |

**Everything L0 emits is a verifiable anchor.** If it can't point at a file/line/manifest
as evidence, it doesn't belong in L0.

---

## 2. Design principle: parse what you can, reason only over the glue

Grounding must be trustworthy, so **prefer deterministic parsers over the LLM.** Manifests,
build descriptors, and migration dirs are *parsed by code* (high confidence, reproducible).
The LLM agent is used only for the genuinely ambiguous glue:

- which code directory corresponds to which deployable,
- canonical-ish naming when signals disagree,
- low-confidence edge inference from naming when no explicit config ref exists.

> Rule of thumb: **if a regex/parser can answer it, the LLM never sees it.** The agent
> reconciles parser *outputs*; it does not read raw source to guess structure.

---

## 3. Signals → anchors (detection table)

Strongest signal first. Each detected anchor carries `evidence` (file/line) + a confidence tier.

| Signal source | Detects | Anchor(s) emitted | Tier |
|---|---|---|---|
| **Deploy manifests** — k8s Deployment/StatefulSet/Service, Helm, docker-compose service, ECS task def, Cloud Run, `serverless.yml` | A *running* component + its image + env | `k8s_workload`, `service_identity` | **verified** |
| **Dockerfile + build context** (+ CI that builds image X from dir Y) | image ↔ code join | binds `path`→`workload` | **verified** |
| **Build descriptors** — `package.json`, `pyproject.toml`, `go.mod`, `pom.xml`, `*.csproj` (+ entrypoint detection: a *server* boot vs a library) | A code unit; service-vs-lib | `repo`, `path` | **strong** |
| **IaC datastore decls** — Terraform `aws_db_instance` / `google_sql_database_instance`, RDS, Helm DB charts | A datastore node | `db_instance` | **verified** |
| **DB bindings in code** — `DATABASE_URL`/`*_DB_HOST` env, ORM config (Prisma, SQLAlchemy, ActiveRecord `database.yml`, Hibernate), migration dirs (Alembic, Flyway, Liquibase) | service ↔ datastore | `reads_from`/`writes_to` edge | **strong** |
| **Cross-service refs** — k8s Service DNS in config, env var URLs to another service, gRPC/OpenAPI client stubs, internal HTTP base URLs | service → service | `calls`/`depends_on` edge | **strong** |
| **Queue/topic refs** — Kafka/SQS/PubSub topic names in config | service ↔ topic | edge | **strong** |
| **Naming/heuristic only** (no config ref) | guessed grouping or edge | any | **inferred** |

**Confidence → trust rule:** only `verified` and `strong` anchors power *click-into-code*.
`inferred` anchors are surfaced in the graph but **flagged**, never used to claim ground truth.

---

## 4. The agent crew (ADK)

Small crew. Deterministic tools do the parsing; one reasoning agent reconciles.

```
survey_agent (coordinator)
├── tools (deterministic FunctionTools — no LLM):
│     list_repo_tree(path)
│     parse_build_descriptors(path)   -> code units + entrypoints
│     parse_deploy_manifests(path)    -> deployables + images + env
│     find_dockerfiles(path)          -> build contexts (image↔dir)
│     find_db_bindings(path)          -> conn strings, ORM, migrations
│     find_service_refs(path)         -> cross-service / topic references
│
├── reconciler (LLM sub-agent):
│     input  = all tool outputs for a repo
│     output = candidate components: group co-located signals into ONE component,
│              pick name candidates, assign kind, attach anchors w/ evidence+tier
│
└── edge_inferencer (LLM sub-agent):
      input  = service_refs + db_bindings + the candidate components
      output = candidate edges with type + evidence + tier
```

> **Local reconciliation ≠ L1 resolution.** Binding a Dockerfile + a k8s Deployment + a
> code dir that obviously go together is a *local, hard* join L0 can do. Merging
> `auth-service` ≡ "login backend" across *prose* is the cross-source, soft problem — that
> stays in L1.

---

## 5. Output contract (what L0 hands to L1)

```jsonc
SurveySnapshot {
  scope: ["repo:org/auth-service", "repo:org/checkout", ...],
  scanned_at: "2026-06-05T...",
  components: [{
    provisional_id,                 // keyed on strongest STABLE anchor (e.g. workload name
                                    //   or repo path) so re-scans are idempotent
    name_candidates: ["auth-service", "auth"],
    kind: "service" | "datastore" | "queue" | "job" | "external",
    anchors: [{ type, value, evidence: "file:line", confidence_tier }]
  }],
  edges: [{
    from_provisional_id, to_provisional_id,
    type: "calls" | "depends_on" | "reads_from" | "writes_to",
    anchors: [{ evidence, confidence_tier }]
  }]
}
```

L0 emits **provisional** ids and **name candidates**, not a final canonical name — L1 owns
canonicalization and the stable `node.id`.

---

## 6. Continuous / incremental behavior

- **Trigger:** git push webhook (or scheduled re-scan). The crew is a *standing* survey, not
  a one-shot import.
- **Idempotent:** anchors keyed on stable identifiers ⇒ re-scanning an unchanged repo
  produces the same `provisional_id`s and the same anchors.
- **Drift:** diff new snapshot vs last → `added` / `changed` / `removed` anchors. A
  `removed` anchor (workload deleted, dir gone) emits a **staleness signal** to L1 — it does
  *not* silently drop the node; L1 decides whether to retire it.

---

## 7. v1 cut

**In:** repo-resident sources — git repos (polyrepo list and/or monorepo root) + any IaC
*files in those repos*. Component + DB + edge detection per the table above. Output snapshot
to L1's store.

**Out (v1):**
- Live cloud API scanning (AWS/GCP `describe` calls) — start with IaC-in-repo; add live
  cloud later as a higher-confidence corroborator.
- Any prose. L0 stays objective.

**Stack-specific caveat:** the scanner tools (`parse_build_descriptors`, `find_db_bindings`,
etc.) are **ecosystem-specific**. They must be written against *your team's actual stack*
(languages, deploy target, DB tech) — that's the one input needed before implementation.

---

## 8. Definition of done (the demo that proves L0)

Point the survey agent at the team's repos and, with **zero prose ingestion**, get an
**auto-drawn, evidence-backed architecture map**: every node clickable to the exact
file/manifest that proves it exists, every edge traceable to the config line that proves it.
That artifact — current, self-built, verifiable — is independently shippable and is the
floor the rest of Cartograph stands on.
```

