@echo off
rem Stop this project's backend processes -- the API on port 8000 and the
rem sync, document and IFC workers -- and nothing else.
rem
rem Three kinds of process, in this order:
rem   1. the children of the backend processes: uvicorn --reload serves the
rem      API from a multiprocessing child whose command line does not name
rem      uvicorn, and a worker reads files in reader processes of its own;
rem      killing only the parent leaves such a child alive, holding port
rem      8000 and running the code it started with (2026-09-27: two orphaned
rem      API children from earlier restarts kept answering with old code);
rem   2. the backend processes themselves: command lines running app.workers.*
rem      or uvicorn app.main from THIS backend folder;
rem   3. orphaned multiprocessing children of this venv's own interpreter
rem      whose parent is gone -- leftovers of an earlier stop; a living
rem      parent's children were handled in 1.
rem Other Python programs and another checkout's processes are left alone.
rem start-backend.bat and start.bat call this first.
setlocal
set "BACKEND=%~dp0backend"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-backend.ps1" -Backend "%BACKEND%"
endlocal
