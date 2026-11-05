"""Execute the portable history SQL in a real SQL engine.

SQLite exercises the production predicates/upserts rather than duplicating
them in a fake. PostgreSQL driver and server behavior remain separate concerns.
"""

import sqlite3

from backend.db.repositories.watch_history import (
    _CLAIM_ANONYMOUS_SQL,
    _SELECT_FOR_OWNER_SQL,
    _SELECT_FOR_USER_SQL,
)


def test_account_creation_and_claim_hide_watches_from_anonymous_sql_listing() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("""CREATE TABLE watch_history (
            watch_id TEXT, owner_client_id TEXT, user_id TEXT,
            updated_at TEXT, watch_json TEXT
        )""")
        db.executemany(
            "INSERT INTO watch_history VALUES (?, 'browser', ?, '2026-11-05', ?)",
            [("account-watch", "account-a", "account-watch"),
             ("anonymous-watch", None, "anonymous-watch")],
        )
        anonymous = {"1": "browser", "2": 100}
        assert db.execute(_SELECT_FOR_OWNER_SQL, anonymous).fetchall() == [("anonymous-watch",)]
        db.execute(_CLAIM_ANONYMOUS_SQL, {"1": "browser", "2": "account-a"})
        assert db.execute(_SELECT_FOR_OWNER_SQL, anonymous).fetchall() == []
        account = db.execute(_SELECT_FOR_USER_SQL, {"1": "account-a", "2": 100})
        assert {r[0] for r in account} == {"account-watch", "anonymous-watch"}
