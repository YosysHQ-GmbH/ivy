from __future__ import annotations

import abc
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import yosys_mau.task_loop as tl
from yosys_mau.stable_set import StableSet

from ..config import App
from ..data import IvyEntity, IvyName, IvyTaskName, Status


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
    solver_finished: bool
    already_solved: bool

    def __init__(self, task_name: IvyTaskName, solver_args: str):
        super().__init__(name=f"{task_name.name}({task_name.solver!r})")

        self.task_name = task_name
        self.solver_args = solver_args
        self.filename = App.data.task_filenames[task_name]
        self.entity = App.data[task_name.name]
        self.solver_finished = False
        self.already_solved = False
        self.discard = False
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

        status = await self.on_solve()
        self.solver_finished = True
        ProofStatusEvent(self.task_name, status).emit()

    def cancel(self, already_solved: bool = False):
        self.already_solved |= already_solved
        super().cancel()

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
                        sentinel.discard = False
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
                        sentinel.discard = False
                        sentinel.set_error_handler(None, lambda err: None)
                        for positive_task in self._positive_priority_tasks[name.name]:
                            sentinel.depends_on(positive_task)
                    task.depends_on(sentinel)
                self._negative_priority_tasks[name.name].add(task)

            task.handle_error(lambda exception: self._error_handler(task, exception))

            return task

    def _error_handler(self, task: IvySolver, err: BaseException) -> None:
        if isinstance(err, tl.TaskFailed) and isinstance(err.task, IvySolver):
            tl.log_exception(err, raise_error=False)
            ProofStatusEvent(task.task_name, "error").emit()
            return
        if isinstance(err, tl.TaskCancelled) and isinstance(err.task, IvySolver):
            if not err.task.already_solved:
                ProofStatusEvent(task.task_name, "pending").emit()
            return
        if isinstance(err, tl.TaskCancelled):
            return
        tl.log_exception(err)

    def cancel_proof_tasks(self, name: IvyName, already_solved: bool) -> None:
        with self.as_current_task():
            for task in self._tasks[name]:
                if not task.solver_finished:
                    task.cancel(already_solved=already_solved)

            del self._tasks[name]


@dataclass
class ProofStatusEvent(tl.TaskEvent):
    name: IvyTaskName
    status: Status


_register_solvers()
