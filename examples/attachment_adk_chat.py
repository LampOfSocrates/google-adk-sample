"""Two ways to put a file in front of the LLM with ADK: as a BLOB or as TEXT.

A user message (`types.Content`) can carry more than text — each entry in `parts`
is a `types.Part`, and a Part can hold raw bytes (a "blob") as well as text. So you
attach a file by adding an extra Part next to your question:

  1. As a BLOB  — `types.Part.from_bytes(data=..., mime_type=...)`. The model
     ingests the file natively (a PDF's layout/tables, an image, etc.). Native PDF
     understanding is GEMINI-ONLY; the LiteLlm backends (openai/deepseek/bedrock)
     and mock won't accept a raw PDF blob. Inline blobs are for smallish files
     (~20 MB/request); for larger files upload via the Files API and attach with
     `types.Part.from_uri(file_uri=..., mime_type=...)` instead.

  2. As TEXT    — read the file and inline its characters in a text Part. Portable
     (works on ANY backend) and usually the right call for CSV / plain text, since
     the model just reads the content.

Needs a GOOGLE_API_KEY (model is gemini-2.0-flash). Run:

    python examples/attachment_adk_chat.py
"""
import asyncio
import itertools
import os
import tempfile
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

APP_NAME = "attachments"

agent = LlmAgent(
    name="reader",
    model="gemini-2.0-flash",
    instruction="You answer questions about the file the user attaches. Be concise.",
)
session_service = InMemorySessionService()
runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

_sid = itertools.count()  # a fresh session per ask, so each demo turn is independent


async def _run(question: str, *attachments: types.Part) -> str:
    """One single-turn ask: a text Part (the question) + any attachment Parts."""
    session_id = f"s{next(_sid)}"
    await session_service.create_session(
        app_name=APP_NAME, user_id="u1", session_id=session_id
    )
    # The attachment(s) ride along in the SAME user message as the question.
    msg = types.Content(role="user", parts=[types.Part(text=question), *attachments])
    reply = ""
    async for event in runner.run_async(
        user_id="u1", session_id=session_id, new_message=msg
    ):
        if event.is_final_response():
            reply = event.content.parts[0].text
    return reply


async def ask_with_blob(path: str, question: str, mime_type: str) -> str:
    """Attach a file as raw bytes (a blob) — the model reads it natively.

    `mime_type` tells the model what the bytes are, e.g. 'application/pdf',
    'text/csv', 'image/png'. PDFs are Gemini-only; CSV/text/images are broader."""
    part = types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=mime_type)
    return await _run(question, part)


async def ask_with_text(path: str, question: str) -> str:
    """Attach a file by inlining its text into the prompt — portable across every
    backend; ideal for CSV / plain-text files."""
    contents = Path(path).read_text(encoding="utf-8")
    return await _run(f"{question}\n\n--- {Path(path).name} ---\n{contents}")


async def main():
    # --- a PDF as a BLOB (native multimodal; Gemini only) ---------------------
    pdf = "tests/fixtures/risk_report.pdf"  # committed test fixture
    print("== PDF as a blob (application/pdf) ==")
    print(await ask_with_blob(pdf, "What is this document about? Name two of its tables.",
                              "application/pdf"))

    # --- a CSV, shown BOTH ways (write a tiny one to a temp file) -------------
    csv_path = os.path.join(tempfile.gettempdir(), "sample_vega.csv")
    Path(csv_path).write_text(
        "region,vega\nAmericas,6384\nEMEA,2100\nAPAC,1200\n", encoding="utf-8"
    )
    try:
        print("\n== CSV as text (portable — works on any backend) ==")
        print(await ask_with_text(csv_path, "Which region has the most vega, and by how much?"))

        print("\n== CSV as a blob (text/csv) ==")
        print(await ask_with_blob(csv_path, "Which region has the most vega?", "text/csv"))
    finally:
        os.remove(csv_path)


if __name__ == "__main__":
    asyncio.run(main())
