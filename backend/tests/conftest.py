"""pytest 共享 fixture：临时数据库"""
import pytest

import backend.storage.db as db


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 DB_PATH 指向临时文件并建表，每个测试独享一个库"""
    db_path = tmp_path / "test_kb.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return str(db_path)
