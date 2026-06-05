"""Pydantic schemas shared across apps.

Why these live here: an ADK `LlmAgent(output_schema=...)` runs in *controlled
generation* mode — ADK forces the model to emit JSON matching the schema and
validates it before your code ever sees it. Defining the schema once here means
the producing agent (text_to_diagram's triad_extractor) and any downstream
consumer agree on the exact shape, with no hand-written JSON parsing.
"""
from pydantic import BaseModel, Field


class Triad(BaseModel):
    """A single knowledge-graph edge: (subject) --predicate--> (object)."""

    subject: str = Field(description="The entity the statement is about.")
    predicate: str = Field(description="The relationship / verb linking subject to object.")
    object: str = Field(description="The entity the subject is related to.")


class TriadList(BaseModel):
    """The whole extraction result. Used as an LlmAgent.output_schema, so the
    model is constrained to return exactly this JSON shape."""

    triads: list[Triad] = Field(default_factory=list)


# --- conversational graph builder (graph_builder app) ---------------------
# The moat is entity resolution + accretion: many turns / sources mention the
# SAME component under different surface names, and must converge onto one node.
# Extraction and resolution are deliberately TWO schemas / TWO agent stages so
# the hard part (resolution against the existing graph) is isolated and testable.


class Mention(BaseModel):
    """One entity as it was *written* in this turn — surface form, not canonical.

    `name` is verbatim ("login backend", "AuthN") so the resolver can judge
    whether it denotes an already-known node. Resolution does NOT happen here.
    """

    name: str = Field(description="The component exactly as named in the text.")
    kind: str = Field(
        default="unknown",
        description="service | datastore | queue | job | external | unknown.",
    )
    claims: list[str] = Field(
        default_factory=list,
        description="Facts this text asserts about the entity (one per item).",
    )


class RelationMention(BaseModel):
    """A relationship between two surface names, to become an edge after both
    endpoints are resolved to node ids."""

    source_name: str = Field(description="Subject, as named in the text.")
    predicate: str = Field(description="calls | depends_on | reads_from | writes_to | ...")
    target_name: str = Field(description="Object, as named in the text.")


class MentionList(BaseModel):
    """Stage-1 (extractor) output: what THIS turn says, pre-resolution."""

    entities: list[Mention] = Field(default_factory=list)
    relations: list[RelationMention] = Field(default_factory=list)


class Resolution(BaseModel):
    """Stage-2 (resolver) verdict for one extracted entity: is it an existing
    node or a new one? This is the moat — `reason`/`confidence` exist so the
    decision is explainable and a human can override it."""

    mention_name: str = Field(description="Echo of the surface name being resolved.")
    decision: str = Field(description='"attach" (existing node) or "new".')
    node_id: str | None = Field(
        default=None,
        description="When decision='attach', the id of the existing node; else null.",
    )
    canonical_name: str = Field(
        description="Matched node's name (attach) or proposed canonical name (new).",
    )
    confidence: float = Field(description="0.0–1.0 confidence in this decision.")
    reason: str = Field(description="Why — the evidence for attach vs new.")


class ResolutionList(BaseModel):
    """Stage-2 (resolver) output: one Resolution per extracted entity."""

    resolutions: list[Resolution] = Field(default_factory=list)
