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
