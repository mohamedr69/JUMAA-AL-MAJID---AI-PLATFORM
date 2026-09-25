"""The BOQ as per IFC drawings, behind the router (app/routers/ifc_boq.py).

  upload.py            a file streamed to the staging folder, hashed on the way in
  revisions.py         which drawing a file is, at which revision; the chain kept whole
  library.py           writing the symbol library: who decided, aliases kept honest
  symbol_matching.py   what Python can say about a symbol nobody has answered yet
  ai_symbol_review.py  what the AI can say about what Python could not
  processing.py        one drawing read: convert, extract, classify, save, file
  zip_import.py        a zip of the building, a floor at a time
  runners.py           the worker's side: the jobs that run all of the above
"""
