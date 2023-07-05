from __future__ import annotations

import json
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Collection, Container, Literal

from yosys_mau import task_loop as tl

from .data import IvyName

Status = Literal["pending", "scheduled", "running", "pass", "fail", "error", "unknown"]


def _transaction(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapper(self: IvyStatusDb, *args: Any, **kwargs: Any) -> Any:
        try:
            tl.log_debug(f"begin {method.__name__!r} transaction")
            self.db.execute("begin")
            result = method(self, *args, **kwargs)
            self.db.execute("commit")
            tl.log_debug(f"comitted {method.__name__!r} transaction")
            return result
        except sqlite3.OperationalError as err:
            tl.log_debug(f"failed {method.__name__!r} transaction", err)
            self.db.rollback()
        except Exception as err:
            tl.log_debug(f"failed {method.__name__!r} transaction", err)
            self.db.rollback()
            raise
        try:
            tl.log_debug(f"retrying {method.__name__!r} transaction once in immediate mode")
            self.db.execute("begin immediate")
            result = method(self, *args, **kwargs)
            self.db.execute("commit")
            tl.log_debug(f"comitted {method.__name__!r} transaction")
            return result
        except Exception as err:
            tl.log_debug(f"failed {method.__name__!r} transaction", err)
            self.db.rollback()
            raise

    return wrapper


class IvyStatusDb:
    def __init__(self, path: Path, setup: bool = False):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")

        if setup:
            self._setup()

    @_transaction
    def _setup(self):
        self.db.execute(
            """
                CREATE TABLE proof_status (
                    name TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                );
            """
        )

    @_transaction
    def full_status(self) -> dict[IvyName, Status]:
        cursor = self.db.execute("""SELECT name, status FROM proof_status""")
        return {IvyName.from_db_key(name): status for name, status in cursor}

    @_transaction
    def status(self, names: Collection[IvyName]) -> dict[IvyName, Status]:
        cursor = self.db.execute(
            """
                SELECT name, status FROM proof_status, json_each(?) WHERE name = json_each.value
            """,
            (json.dumps([name.db_key for name in names]),),
        )
        return {IvyName.from_db_key(name): status for name, status in cursor}

    @_transaction
    def initialize_status(self, names: Collection[IvyName]) -> None:
        self.db.executemany(
            """INSERT INTO proof_status (name, status) VALUES (?, 'pending')""",
            [(name.db_key,) for name in names],
        )

    @_transaction
    def change_status(
        self, name: IvyName, new_status: Status, require: Container[Status] | None = None
    ) -> Status | None:
        old_status = self.db.execute(
            """SELECT status FROM proof_status WHERE name = ?""", (name.db_key,)
        ).fetchone()[0]
        if require is not None and old_status not in require:
            return old_status

        self.db.execute(
            """UPDATE proof_status SET status = :status WHERE name = :name""",
            dict(name=name.db_key, status=new_status),
        )
