"""The deterministic pipeline's bookkeeping: what a read covered, what it
could not settle, and how each unsettled thing is routed (`issues.py`), and
the run that ties a document's read to its issues and proposals
(`pipeline.py`). Nothing here calls a model; `app.ai` does that when asked.
"""
