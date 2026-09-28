"""D7: trace every SQL statement of the flaky BOQ job test with the thread that issued it and the connection id,
so the interleaving of the job thread's transaction with the request sessions is on record."""
import os, sys, threading, time, pathlib
sys.path.insert(0, r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
os.chdir(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend")
import tests.conftest  # noqa: E402  (sets the isolated environment before the app is imported)
from sqlalchemy import event  # noqa: E402
from app.database import engine  # noqa: E402

log = open(sys.argv[1], "w", encoding="utf-8")
t0 = time.perf_counter()


@event.listens_for(engine, "before_cursor_execute")
def _trace(conn, cursor, statement, parameters, context, executemany):
    head = statement.strip().split("\n")[0][:90]
    log.write(f"{time.perf_counter() - t0:8.3f}s thread={threading.current_thread().name:<28} conn={id(conn.connection.dbapi_connection):x} {head}\n")


@event.listens_for(engine, "rollback")
def _rb(conn):
    log.write(f"{time.perf_counter() - t0:8.3f}s thread={threading.current_thread().name:<28} conn={id(conn.connection.dbapi_connection):x} ROLLBACK (pool/session)\n")


@event.listens_for(engine, "commit")
def _cm(conn):
    log.write(f"{time.perf_counter() - t0:8.3f}s thread={threading.current_thread().name:<28} conn={id(conn.connection.dbapi_connection):x} COMMIT\n")


import pytest  # noqa: E402
rc = pytest.main(["tests/test_ai_sheet_reader.py::test_the_first_read_runs_as_a_job_the_page_follows", "-q", "-p", "no:cacheprovider", "--basetemp=C:/t/m2r/trace"])
log.close()
print("pytest rc", rc)
