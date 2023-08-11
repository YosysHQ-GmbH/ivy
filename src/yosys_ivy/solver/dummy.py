from __future__ import annotations

import argparse
import asyncio
import shlex
from typing import Any

import yosys_mau.task_loop as tl

from yosys_ivy.data import Status
from yosys_ivy.solver import IvySolver

argument_parser = argparse.ArgumentParser(prog="dummy", add_help=False)
argument_parser.error = tl.log_error  # type: ignore
argument_parser.add_argument("status", choices=["pass", "fail", "error", "unknown"])
argument_parser.add_argument("-d", "--delay", type=float, default=0.0)


class IvyDummy(IvySolver):
    status: Status
    delay: float

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.use_lease = True

        with self.as_current_task():
            parsed_args = argument_parser.parse_args(shlex.split(self.solver_args))
            self.status = parsed_args.status
            self.delay = parsed_args.delay

    async def on_solve(self) -> Status:
        tl.log_warning("using dummy solver, does not actually run anything")

        await asyncio.sleep(self.delay)

        return self.status
