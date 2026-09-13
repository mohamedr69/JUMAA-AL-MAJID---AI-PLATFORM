"""Selective AI assistance.

One provider (`provider.py`), one evidence builder per task (`evidence.py`),
one proposal schema with validation that does not trust the model
(`proposals.py`), a content-keyed result cache (`cache.py`) and budgets
(`budget.py`). The deterministic pipeline decides *whether* to ask; this
package decides *how*, and never writes a value into the project itself.
"""
