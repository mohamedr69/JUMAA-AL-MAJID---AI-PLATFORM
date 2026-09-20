"""BOQ as per IFC drawings: fire alarm devices counted off an
issued-for-construction DWG or DXF, by what each symbol looks like rather
than its block name, with every symbol the library does not know verified
by an engineer before any quantity is given.

Ported from the standalone "BOQ Extraction" tool. The reading and matching
(`dxf/`, `resolve`, `reprocess`) are that tool's, unchanged; what is the
platform's is where things live -- a drawing belongs to a project, the
symbol library is shared by every project and written through to the
company library (`library_file`).
"""
