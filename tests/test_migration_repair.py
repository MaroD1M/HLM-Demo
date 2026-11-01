import logging
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.services.migration_service import MigrationService


def _service(db):
    return MigrationService(
        db,
        logging.getLogger('test-migration-repair'),
        lambda: '',
        lambda: 'test',
        lambda _key, default=None: default,
        lambda *_args, **_kwargs: (True, ''),
    )


def test_v17_repairs_downloader_columns_missing_from_v13(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'compat.db'}")
    with Session(engine) as session:
        session.execute(text('CREATE TABLE schema_meta (key VARCHAR(100) PRIMARY KEY, value VARCHAR(200))'))
        session.execute(text('CREATE TABLE downloader (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100) NOT NULL, host VARCHAR(200) NOT NULL, port INTEGER NOT NULL)'))
        session.execute(text("INSERT INTO downloader(name, host, port) VALUES ('legacy', '127.0.0.1', 8080)"))
        session.execute(text("INSERT INTO schema_meta(key, value) VALUES ('db_schema_version', '16')"))
        session.commit()

        svc = _service(SimpleNamespace(session=session, text=text))
        svc.ensure_compat_columns()

        assert svc.get_schema_meta_value('db_schema_version') == '17'
        columns = {row[1] for row in session.execute(text('PRAGMA table_info(downloader)')).fetchall()}
        assert {'category', 'session_ttl_seconds'} <= columns
        row = session.execute(text('SELECT category, session_ttl_seconds FROM downloader')).one()
        assert row == ('', None)

        svc.ensure_compat_columns()

        columns = {row[1] for row in session.execute(text('PRAGMA table_info(downloader)')).fetchall()}
        assert {'category', 'session_ttl_seconds'} <= columns
