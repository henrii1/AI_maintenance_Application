import sqlite3

import pytest

from backend.content_db import connect_content, init_content_schema


def test_schema_and_fts(tmp_path):
    db = tmp_path / "content.db"
    conn = connect_content(db)
    init_content_schema(conn)
    init_content_schema(conn)  # idempotent
    conn.execute("INSERT INTO chunks (id, doc_id, section_path, page_start, page_end, text, token_count,"
                 " has_warning, figure_refs) VALUES ('d:0001','d','S',1,1,'Install the crosshead',4,0,'[]')")
    conn.execute("INSERT INTO chunks_fts (chunk_id, section_path, text) VALUES ('d:0001','S','Install the crosshead')")
    conn.commit()
    rows = conn.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH 'crossheads'").fetchall()
    assert [r["chunk_id"] for r in rows] == ["d:0001"]  # porter stemming


def test_readonly_connection_refuses_writes(tmp_path):
    db = tmp_path / "content.db"
    conn = connect_content(db)
    init_content_schema(conn)
    conn.close()
    ro = connect_content(db, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO sets (id, name, equipment_json) VALUES ('x','x','{}')")
