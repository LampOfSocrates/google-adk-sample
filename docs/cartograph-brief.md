# Cartograph — Product Brief

> **Working title: Cartograph.** Cartographer agents continuously survey unmapped
> territory — your code, deployments, and the prose around them — and draw a living
> map that redraws itself as the territory changes. The USP is one sentence: **it maps
> itself.**
>
> Alternates: **Sextant** (fix a component's true identity by triangulating many
> sightings — literally entity resolution) · **Lodestone** (the magnet that pulls
> scattered knowledge onto the right node).

---

## 1. What it is (in one paragraph)

A **self-building knowledge graph of your system.** A fleet of agents continuously
(a) **scans repos + deployments** to discover what components exist and which code/DB
each one *is* — the hard, verifiable skeleton — and (b) **ingests unstructured text**
(architecture comments, email, wiki, incident write-ups, chat) to enrich each node with
human knowledge, attributed and dated. The diagram is just the **view**. You click a
component and drill to its code; you click a datastore and see its schema and sample
rows; you wash incident history over the map and see which parts of the system hurt.

The thing nobody else does: **the graph populates itself from the artifacts you already
have, instead of from manifests you have to write and maintain.**

---

## 2. The core insight / USP

Every existing "system map" is a graph **you populate manually** (write a
`catalog-info.yaml`, draw in Lucid, tag services). They are accurate the day you write
them and decay every day after.

Cartograph inverts this:

1. **Grounding is agent-discovered, not declared.** A survey agent scans the whole repo
   + deployment surface and answers "which system is which code" by *reading reality*,
   not by trusting a manifest. So the skeleton is always current and always clickable-to-truth.
2. **Knowledge accretes from many sources onto one canonical node.** Five different
   mentions across email, wiki, and an incident — "auth-service", "login backend",
   "AuthN", "Identity Provider" — resolve to **one node** and merge their facts, each
   fact carrying who-said-it and when.
3. **Every fact is a claim with provenance.** Nothing is "just true." Hard anchors
   (verifiable, from code/deploy) and soft knowledge (accreted from prose, attributed)
   are kept distinct, so a click into code lands somewhere *true* while institutional
   knowledge stays *traceable and ageable*.

**Pitch in one line:** *Backstage that builds itself from your code and your text,
instead of from YAML you have to write.*

---

## 3. Market landscape — what they do, and where they stop

| Product | What it does well | Where it stops (our opening) |
|---|---|---|
| **Backstage** (Spotify, OSS) | Rich software catalog, component graph, plugins, drill-in, ownership. | **Input is hand-written `catalog-info.yaml` per service.** No prose ingestion, no self-discovery. You maintain it forever. |
| **Port / Cortex / OpsLevel / Atlassian Compass** | Managed IDPs; scorecards, ownership, integrations. | Same core limit — **structured input you curate.** Catalog is only as fresh as the team's discipline. |
| **Datadog Service Catalog / Service Map**, **New Relic** | Live service graph **fused with incident/telemetry** — strong on "what's hurting now." | Graph is **runtime-derived only** (traces/metrics). No design intent, no wiki/email/architecture *knowledge*, no code-level or schema drill-in from text. |
| **Lucidchart / Miro / Whimsical / Eraser** AI diagramming | Fast prose→diagram; nice canvases. | **One-shot pictures.** No persistent graph, no entity resolution across inputs, no grounding to real code/DB, no incident overlay. The diagram *is* the product. |
| **Mermaid AI / Diagramming AI / DiagramGPT** | NL → DSL → rendered, editable intermediate. | Same — **stateless generation.** No memory, no accretion, no ground truth. |
| **Sourcegraph / Swimm / CodeSee** | Deep code understanding & code maps. | **Code-only.** No fusion with prose knowledge, incidents, or DB; not a system-level living graph. |
| **dbdocs / Atlas / SchemaSpy** | DB schema docs & diagrams. | **DB-only, static.** Disconnected from the system graph and from why the DB exists. |

**The gap, stated plainly:** the IDPs have the *graph* but require *manual input*; the
observability tools have the *live signal* but no *design knowledge or drill-in*; the AI
diagrammers have *prose-in* but no *memory or ground truth*; the code/DB tools are
*single-domain*. **Nobody fuses all four** — self-discovered grounding + accreted prose
knowledge + incident overlay + code/DB drill-in — **into one living graph.** That fusion
is the product.

---

## 4. Why we can do it better

- **Self-grounding survey agents** → the skeleton is always true and always current
  (no manifest rot). This is also what makes "click → code" and "click DB → schema"
  trustworthy, because the anchor was *verified against reality*, not inferred.
- **Entity resolution as a first-class engine** → many sources, one canonical node.
  This is the hard moat; incumbents sidestep it by demanding pre-structured input.
- **Claim + provenance data model** → we can *show our work*, age out stale prose, and
  flag conflicts. Trust is a feature, not an afterthought.
- **Agentic + continuous** → it's not a batch import, it's a standing crew that keeps
  re-surveying and re-ingesting. The graph trends *more* correct over time, not less.
- **One graph, many overlays** → incidents are just one signal layer; ownership, churn,
  cost, and freshness are the same mechanism. The platform generalizes.

---

## 5. Architecture — refined layer stack

Read bottom-up. Each layer is independently buildable; lower layers are prerequisites
for the drill-in promises of higher ones.

| # | Layer | What it does | Built from | Difficulty | Independently useful? |
|---|---|---|---|---|---|
| **L0** | **Survey / Grounding** | Scan repos + deployments → discover components and bind each to its real code / workload / DB instance. Produces the **hard-anchored skeleton**. | Repo scan agent, deploy/k8s scan agent | High (but objective) | **Yes** — auto-current architecture map from code, on its own |
| **L1** | **Graph core** | Canonical node/edge store. Claim + provenance model. **Entity resolution** lives here. | Graph DB + resolver service | High (the moat) | No (substrate) |
| **L2** | **Ingestion agents** | Pull from wiki / email / chat / incidents → extract claims → resolve to nodes → write with provenance. Continuous. | One agent per source + extractor | Medium | No (feeds L1) |
| **L3** | **Signal / overlays** | Compute overlays on the graph: incident frequency ("what hurts"), ownership, churn, freshness. | Aggregations over L1 + L2 | Medium | Yes (as a lens) |
| **L4** | **Views & drill-in** | The diagram view; click component → code; click DB → schema + sample data. | Diagram render + code/DB connectors | Medium | Yes (the face) |
| **L5** | **Query / chat** | Ask the graph: "what's most fragile near checkout?" Natural-language Q&A over the KG. | Retrieval over L1 | Medium | Yes |

**Changes from the first table:** "parse text" split into L0 (grounding, the new
foundation you added) vs L2 (prose ingestion); entity resolution promoted to its own
core engine (L1); DB drill-in correctly isolated as an L4 *connector*, not part of the
text pipeline.

---

## 6. The node data model (the piece everything hangs off)

The central design choice is the **hard/soft split with provenance on every fact.**

```jsonc
Node {                          // a canonical component
  id,                           // stable internal id (never changes)
  canonical_name,
  aliases: ["auth-service", "login backend", "AuthN", "Identity Provider"],
  kind: "service" | "datastore" | "queue" | "job" | "external" | ...,

  // HARD — verifiable, agent-discovered from code/deploy. Powers click-into-truth.
  anchors: [{
    type: "repo" | "path" | "service_identity" | "openapi" | "k8s_workload" | "db_instance",
    value: "github.com/org/auth-service",
    verified_by: "survey-agent",
    verified_at: "2026-06-05",
    evidence: "commit_sha / manifest path",   // how it was proven
    confidence: 1.0
  }],

  // SOFT — accreted from prose. Each is a CLAIM, never bare truth.
  knowledge: [{
    claim: "owns user password hashing",
    source: { type: "wiki", url, author, timestamp },
    extracted_by: "ingest-agent",
    extracted_at: "2026-06-05",
    confidence: 0.7,
    status: "active" | "superseded" | "disputed",
    supersedes: "claim_id"                     // recency / staleness handling
  }],

  // OVERLAYS — time-stamped signals (incidents are just the first kind).
  signals: [{
    type: "incident", ref: "INC-1234", role: "root_cause" | "affected",
    severity, timestamp
  }]
}

Edge {                          // relationships are first-class & also hard/soft
  from, to,
  type: "depends_on" | "calls" | "reads_from" | "writes_to" | "owned_by",
  anchors: [...],               // e.g. proven by an actual import / network call
  knowledge: [...]              // e.g. "team says this is being deprecated"
}
```

**Invariants that make it work:**
- **No bare facts.** Every node/edge fact is either a verified *anchor* or an attributed
  *claim*. This is what lets us age, dispute, and explain.
- **Anchors ground the clicks; knowledge gives context.** A click-into-code follows an
  anchor (true) — never a claim (guessed).
- **Aliases drive resolution.** The resolver's job per new mention: attach to the right
  node, or propose a new one. Wrong merges and wrong splits are the two failure modes to
  obsess over.
- **Claims supersede, they don't overwrite.** History is retained; the graph can answer
  "what did we believe in March, and why did it change?"

---

## 7. What to start with

**Build L0 first — the survey/grounding agent — because it is the foundation *and* the
fastest standalone win.** Reasons:

1. It's **objective**: it scans real code/deploys, so output is verifiable and bootstraps
   trust before any fuzzy prose is involved.
2. It is **independently useful on day one**: an always-current architecture map auto-drawn
   from your repo is a demo-able product *with zero prose ingestion*.
3. It produces the **anchor skeleton** that everything else attaches to — without it there's
   nothing to enrich and nothing to click into.

**Then the thinnest end-to-end slice (the "wow" MVP):**

```
L0  Survey agent:  repo + deploy scan  → hard-anchored node graph
L1  Graph core:    minimal node/edge store + a first-cut resolver (alias match)
L2  ONE source:    incidents only      → claims + "what hurts" signal
L4  View:          rendered diagram     + click component → code
```

**Deliberately deferred for v1:** email + wiki ingestion (noisy; add after the resolver
is proven on the cleaner incident source), DB schema/sample-data drill-in (separate
connector + governance/PII problem), L5 chat-over-graph.

**Why incidents as the *first* prose source** (not email): they're the highest-signal,
most-structured text you have, and they light up the single most compelling overlay —
"which components have more problems" — immediately. Email is the noisiest source and the
hardest resolution test; earn it later.

**ADK fit:** the "agents that go and enrich" map directly onto this repo's multi-agent
ADK setup — the survey agent, the per-source ingest agents, and the resolver are exactly
the kind of crew ADK orchestrates. The existing **Text-to-Diagram** pane stops being a
one-shot renderer and becomes the **view onto a persistent graph the agents keep
enriching.** Cartograph is the destination Text-to-Diagram was already pointing at.

---

## 8. Scope decisions (settled 2026-06-05)

1. **Audience: own team first.** Single-tenant / internal. We control the repos,
   deployments, and incident feed, so the survey agent grounds against real reality.
2. **Shape: standalone graph + view.** Cartograph owns the graph, overlays, and diagram
   UI end-to-end. (Revisit a Backstage/Datadog feeder play later if distribution stalls.)
3. **First build: the L0 survey agent.** See `l0-survey-agent.md`.
```

