from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Collection, Container, Iterable, TypeVar

from yosys_mau import task_loop as tl

from .data import IvyData, IvyName, IvyTaskName, Status, status_or_equivalent

Fn = TypeVar("Fn", bound=Callable[..., Any])


def transaction(method: Fn) -> Fn:
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

    return wrapper  # type: ignore


class IvyStatusDb:
    status_ticks: int

    def __init__(self, path: Path, setup: bool = False, timeout: float = 5.0):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=timeout)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=0")

        self.status_ticks = 0

        if setup:
            self._setup()

    @transaction
    def _setup(self):
        self.db.execute(
            """
                CREATE TABLE proof_status (
                    name TEXT,
                    solver TEXT,
                    status TEXT NOT NULL,
                    PRIMARY KEY (name, solver)
                );
            """
        )

    @transaction
    def full_status(self, data: IvyData) -> dict[IvyTaskName, Status]:
        cursor = self.db.execute("""SELECT name, solver, status FROM proof_status""")
        return {
            IvyTaskName(IvyName.from_db_key(data, name), solver): status
            for name, solver, status in cursor
        }

    def reduced_status(self, data: IvyData) -> dict[IvyName, Status]:
        full = self.full_status(data)
        grouped: defaultdict[IvyName, list[Status]] = defaultdict(list)
        for task_name, status in full.items():
            grouped[task_name.name].append(status)

        return {name: status_or_equivalent(*statuses) for name, statuses in grouped.items()}

    @transaction
    def status(self, data: IvyData, names: Collection[IvyName]) -> dict[IvyTaskName, Status]:
        cursor = self.db.execute(
            """
                SELECT name, solver, status FROM proof_status, json_each(?)
                WHERE name = json_each.value
            """,
            (json.dumps([name.db_key for name in names]),),
        )
        return {
            IvyTaskName(IvyName.from_db_key(data, name), solver): status
            for name, solver, status in cursor
        }

    @transaction
    def initialize_status(self, names: Collection[IvyTaskName]) -> None:
        self.db.executemany(
            """
                INSERT INTO proof_status (name, solver, status)
                VALUES (:name, :solver, 'pending')
            """,
            [dict(name=task_name.name.db_key, solver=task_name.solver) for task_name in names],
        )

    def change_status(
        self, name: IvyTaskName, new_status: Status, require: Container[Status] | None = None
    ) -> Status | None:
        return self.change_status_many([name], new_status, require).get(name, None)

    @transaction
    def change_status_many(
        self,
        names: Iterable[IvyTaskName],
        new_status: Status,
        require: Container[Status] | None = None,
    ) -> dict[IvyTaskName, Status]:
        results: dict[IvyTaskName, Status] = {}
        for task_name in names:
            old_status = self.db.execute(
                """SELECT status FROM proof_status WHERE name = :name AND solver = :solver""",
                dict(name=task_name.name.db_key, solver=task_name.solver),
            ).fetchone()[0]

            if require is not None and old_status not in require:
                results[task_name] = old_status
            else:
                self.db.execute(
                    """
                        UPDATE proof_status SET status = :status
                        WHERE name = :name AND solver = :solver
                    """,
                    dict(name=task_name.name.db_key, solver=task_name.solver, status=new_status),
                )

        return results
