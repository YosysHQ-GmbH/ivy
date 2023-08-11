from __future__ import annotations

import abc
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import yosys_mau.task_loop as tl
from yosys_mau.stable_set import StableSet

from ..config import App
from ..data import IvyEntity, IvyName, IvyTaskName, Status, color_status


def _register_solvers():
    from .dummy import IvyDummy
    from .sby import IvySby

    solvers["sby"] = IvySby
    solvers["dummy"] = IvyDummy


solvers: dict[str, Callable[[IvyTaskName, str], IvySolver]] = {}


class IvySolver(tl.Task, abc.ABC):
    task_name: IvyTaskName
    filename: str
    entity: IvyEntity
    priority: int

    def __init__(self, task_name: IvyTaskName, solver_args: str):
        super().__init__(name=f"{task_name.name}({task_name.solver!r})")

        self.task_name = task_name
        self.solver_args = solver_args
        self.filename = App.data.task_filenames[task_name]
        self.entity = App.data[task_name.name]
        self.priority = self.entity.solve_with[task_name.solver] or 0
        with self.as_current_task():
            tl.LogContext.scope = App.data.task_info[task_name]
            tl.priority.JobPriorities.priority = (
                self.priority,
                -self.entity.solve_order[task_name.solver] or 0,
                -self.entity.dependency_order(),
            )

    async def on_run(self):
        ProofStatusEvent(self.task_name, "running").emit()

        status = "error"
        try:
            status = await self.on_solve()
        except asyncio.CancelledError:
            status = "pending"
        finally:
            if status != "pending":
                tl.log("Proof status:", color_status(status))
            ProofStatusEvent(self.task_name, status).emit()

    @abc.abstractmethod
    async def on_solve(self) -> Status:
        ...


@tl.task_context
class SolverContext:
    solvers: Solvers


class Solvers(tl.TaskGroup):
    _positive_priority_tasks: dict[IvyName, StableSet[IvySolver]]
    _negative_priority_tasks: dict[IvyName, StableSet[IvySolver]]
    _tasks: dict[IvyName, StableSet[IvySolver]]
    _priority_sentinels: dict[IvyName, tl.Task]

    def __init__(self):
        super().__init__(name="solvers")
        self._positive_priority_tasks = defaultdict(StableSet)
        self._negative_priority_tasks = defaultdict(StableSet)
        self._tasks = defaultdict(StableSet)
        self._priority_sentinels = {}

        SolverContext.solvers = self

    def dispatch_proof_task(self, name: IvyTaskName) -> IvySolver:
        with self.as_current_task():
            solver = name.solver
            if solver == "default":
                solver = App.config.options.default_solver

            solver_split = solver.split(None, 1)
            if len(solver_split) == 1:
                solver_split.append("")
            solver_name, solver_args = solver_split

            task = solvers[solver_name](name, solver_args)
            self._tasks[name.name].add(task)
            if task.priority > 0:
                if self._negative_priority_tasks[name.name]:
                    if self._positive_priority_tasks[name.name]:
                        sentinel = self._priority_sentinels[name.name]
                    else:
                        sentinel = self._priority_sentinels[name.name] = tl.Task()
                        sentinel.set_error_handler(None, lambda err: None)
                        for negative_task in self._negative_priority_tasks[name.name]:
                            negative_task.depends_on(sentinel)
                    sentinel.depends_on(task)
                self._positive_priority_tasks[name.name].add(task)
            elif task.priority < 0:
                if self._positive_priority_tasks[name.name]:
                    if self._negative_priority_tasks[name.name]:
                        sentinel = self._priority_sentinels[name.name]
                    else:
                        sentinel = self._priority_sentinels[name.name] = tl.Task()
                        sentinel.set_error_handler(None, lambda err: None)
                        for positive_task in self._positive_priority_tasks[name.name]:
                            sentinel.depends_on(positive_task)
                    task.depends_on(sentinel)
                self._negative_priority_tasks[name.name].add(task)

            task.handle_error(lambda exception: self._error_handler(task, exception))

            return task

    def _error_handler(self, task: IvySolver, err: BaseException) -> None:
        if isinstance(err, tl.TaskCancelled):
            return
        raise err

    def cancel_proof_tasks(self, name: IvyName) -> None:
        with self.as_current_task():
            for task in self._tasks[name]:
                task.cancel()

            del self._tasks[name]


@dataclass
class ProofStatusEvent(tl.TaskEvent):
    name: IvyTaskName
    status: Status


_register_solvers()
