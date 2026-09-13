"""Check the configured model end to end, on one drawn cell.

The pipeline's model call is small and specific: a picture of one quantity
cell, and a JSON answer saying what the number is. This makes exactly that
call, against whatever `AI_PROVIDER` / `AI_MODEL_SMALL` say, and reports
what came back -- including the failures worth telling apart: no
credential, no credits, a model that does not take images, a model that
does not honour the schema.

    cd backend
    .\\venv\\Scripts\\python scripts\\ai_selftest.py
    .\\venv\\Scripts\\python scripts\\ai_selftest.py --model gpt-4.1-mini
    .\\venv\\Scripts\\python scripts\\ai_selftest.py --all

It costs one short call per model. Nothing is written to the database and
no project is touched.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.ai.evidence import SYSTEM_READ_CELL  # noqa: E402
from app.ai.proposals import PROPOSAL_SCHEMA, Proposal, parse, validate  # noqa: E402
from app.ai.provider import AiRequest, ImagePart, TextPart, get_provider, set_provider  # noqa: E402
from app.core.config import get_settings  # noqa: E402

TARGET = "boq_line:1:1"
EXPECTED = "74"


def drawn_cell(value: str = EXPECTED) -> bytes:
    """A quantity cell as a scanned sheet prints one: the number between two
    column rules, a little of the row above and below."""
    image = Image.new("L", (180, 64), 255)
    draw = ImageDraw.Draw(image)
    draw.line((8, 2, 8, 62), fill=0, width=2)
    draw.line((172, 2, 172, 62), fill=0, width=2)
    draw.line((8, 4, 172, 4), fill=170, width=1)
    draw.line((8, 60, 172, 60), fill=170, width=1)
    draw.text((78, 26), value, fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def run(model: str | None) -> int:
    settings = get_settings()
    if model:
        # One model for this run only; the provider reads the setting.
        object.__setattr__(settings, "ai_model_small", model)
        set_provider(None)
    provider = get_provider()
    name = model or settings.ai_model_small

    print(f"provider : {type(provider).__name__} ({settings.ai_provider})")
    print(f"model    : {name}")
    if not getattr(provider, "ready", False):
        print(f"result   : NOT READY -- {getattr(provider, 'status', 'no status')}")
        return 2

    png = drawn_cell()
    request = AiRequest(
        task="read_cell",
        system=SYSTEM_READ_CELL,
        parts=[
            TextPart("task", f"target: {TARGET}\nregion label: cell\npage: 1"),
            ImagePart("cell", png),
            TextPart("row", "description: Ceiling Speaker Firedome\ncatalog_no: PC-1860BS-C\nocr_read: (nothing)"),
        ],
        schema=PROPOSAL_SCHEMA,
        max_output_tokens=settings.ai_max_output_tokens_per_task,
    )

    response = provider.complete(request)
    print(f"latency  : {response.latency_ms} ms")
    usage = response.usage
    print(f"tokens   : in={usage.input_tokens} out={usage.output_tokens} "
          f"cached={usage.cached_input_tokens} reasoning={usage.reasoning_tokens}")

    if not response.ok:
        print(f"result   : FAILED ({response.error}) -- {response.error_detail}")
        if response.error == "quota":
            print("           the key is valid; the account needs credits before any call can run.")
        return 1

    parsed = parse(response.data)
    if not isinstance(parsed, Proposal):
        print(f"result   : REPLY REJECTED -- {parsed.reason}")
        print(f"           raw: {response.raw_text}")
        return 1

    verdict = validate(parsed, task="read_cell", allowed_target=TARGET, sent_regions={"cell"},
                       independent_readings={EXPECTED}, word_quantities=set())
    read = verdict.value
    print(f"read     : {read!r} (the cell says {EXPECTED!r})")
    print(f"validated: {verdict.state} -- {verdict.reason}")
    if read == EXPECTED and verdict.state == "validated":
        print("result   : OK -- images, schema and validation all work on this model.")
        return 0
    print("result   : the call worked but the reading was wrong; try another model or raise the crop size.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="Test this model instead of AI_MODEL_SMALL.")
    parser.add_argument("--all", action="store_true", help="Test a shortlist of candidates and report which work.")
    args = parser.parse_args(argv)

    if not args.all:
        return run(args.model)

    if get_settings().ai_provider.lower() == "groq":
        shortlist = ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    else:
        shortlist = ["gpt-5.4-mini", "gpt-5.4", "gpt-5.5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"]
    worked: list[str] = []
    for candidate in shortlist:
        print(f"\n--- {candidate}")
        if run(candidate) == 0:
            worked.append(candidate)
    print("\nmodels that passed:", ", ".join(worked) or "none")
    return 0 if worked else 1


if __name__ == "__main__":
    raise SystemExit(main())
