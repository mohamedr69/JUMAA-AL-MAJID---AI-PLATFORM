"""Stage 1: archive-directory integrity, independent of filesystem scanning."""
import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.database import engine
from app.migrations import ALEMBIC_INI
from app.models import EpArchiveFolder, EpArchiveRoot, Project, User


def key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def archive(db, path="C:/demo/archive"):
    row = EpArchiveRoot(root_key=key(path), root_path=path)
    db.add(row)
    db.flush()
    return row


def folder(db, root, path, number="29495"):
    row = EpArchiveFolder(archive_id=root.id, ep_number=number,
                          folder_name=path.split("/")[-1], relative_path=path, path_key=key(path))
    db.add(row)
    return row


def test_directory_retains_duplicate_ep_numbers_without_creating_projects(db_session):
    root = archive(db_session)
    folder(db_session, root, "Client A/EP-29495 Tower")
    folder(db_session, root, "Client B/EP-29495 Tower")
    db_session.commit()
    matches = db_session.scalars(select(EpArchiveFolder).where(
        EpArchiveFolder.archive_id == root.id,
        EpArchiveFolder.ep_number == "29495",
        EpArchiveFolder.is_available.is_(True),
    )).all()
    assert len(matches) == 2
    assert db_session.query(Project).count() == 0
    assert root.scan_status == "pending"
    assert root.last_successful_scan_at is None


def test_directory_rejects_duplicate_folder_in_same_archive(db_session):
    root = archive(db_session)
    folder(db_session, root, "EP-29495 Tower")
    db_session.commit()
    folder(db_session, root, "EP-29495 Tower")
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(EpArchiveFolder).count() == 1


def test_directory_keeps_identical_relative_paths_in_different_archives(db_session):
    a = archive(db_session, "C:/demo/archive-a")
    b = archive(db_session, "C:/demo/archive-b")
    folder(db_session, a, "EP-29495 Tower")
    folder(db_session, b, "EP-29495 Tower")
    db_session.commit()
    assert db_session.query(EpArchiveFolder).count() == 2


def test_directory_retains_missing_history_without_returning_it_as_available(db_session):
    root = archive(db_session)
    missing = folder(db_session, root, "EP-29495 Old Location")
    missing.is_available = False
    folder(db_session, root, "Client/EP-29495 New Location")
    db_session.commit()
    assert db_session.query(EpArchiveFolder).count() == 2
    assert db_session.query(EpArchiveFolder).filter_by(is_available=True).count() == 1


def test_directory_rejects_an_orphan_and_duplicate_archive(db_session):
    root = archive(db_session)
    db_session.commit()
    orphan = EpArchiveFolder(archive_id=root.id + 999, ep_number="29495", folder_name="EP-29495",
                             relative_path="EP-29495", path_key=key("orphan"))
    db_session.add(orphan)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(EpArchiveRoot(root_key=root.root_key, root_path=root.root_path))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_directory_upgrade_preserves_existing_project_and_adds_lookup_index(db_session):
    user = db_session.query(User).first()
    project = Project(ep_number="stage1-keep", created_by_id=user.id, project_name="Keep this project")
    db_session.add(project)
    db_session.commit()
    db_session.close()
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "c4d5e6f7a8b9")
        assert "ep_archive_folders" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        assert connection.execute(select(Project.project_name).where(
            Project.ep_number == "stage1-keep")).scalar_one() == "Keep this project"
        indexes = inspect(connection).get_indexes("ep_archive_folders")
        assert any(i["name"] == "ix_ep_archive_folders_lookup"
                   and i["column_names"] == ["archive_id", "ep_number", "is_available"] for i in indexes)
