from __future__ import annotations
import argparse

import asyncio
from pathlib import Path
import shlex
from textwrap import dedent
from typing import Any

import yosys_mau.task_loop as tl
from yosys_mau.stable_set import StableSet

from yosys_ivy.config import App
from yosys_ivy.data import IvyName, Status
from yosys_ivy.design import DesignContext
from yosys_ivy.solver import IvySolver


argument_parser = argparse.ArgumentParser(prog="sby", add_help=False)
argument_parser.error = tl.log_error  # type: ignore
argument_parser.add_argument("-d", "--depth", type=int, default=5)


class IvySby(IvySolver):
    sby_file: Path
    sby_dir: Path
    sby_proc: tl.Process

    engine: list[str]
    depth: int

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.use_lease = True
        self.sby_file = App.work_dir / "tasks" / f"{self.filename}.sby"
        self.sby_dir = App.work_dir / "tasks" / f"{self.filename}"

        with self.as_current_task():
            parsed_args, self.engine = argument_parser.parse_known_args(
                shlex.split(self.solver_args)
            )
            self.depth = parsed_args.depth

    async def on_prepare(self):
        self.depends_on(DesignContext.design.prepare_design())

    async def on_solve(self):
        setup = self.entity.step_setup()

        attributes: dict[str, StableSet[IvyName]] = {}

        attributes["ivy_assert"] = setup.asserts
        attributes["ivy_assume"] = setup.assumes
        attributes["ivy_cross_assume"] = setup.cross_assumes

        setattrs = "\n".join(
            f"setattr -set {attr} 1 {' '.join(name.rtlil for name in names)}"
            for attr, names in attributes.items()
            if names
        )

        # TODO we can actually run the chformal commands after SBY's prep step (followed only by
        # opt_clean to remove unused logic), so running the prep step once for all tasks with the
        # same design should give a speedup

        self.sby_file.write_text(
            dedent(
                """\
                # running in {sby_dir}
                [options]
                mode prove
                depth {depth}
                assume_early off

                [engines]
                {engine}
                [script]
                read_rtlil ../../../model/design.il
                uniquify; hierarchy -nokeep_asserts
                {setattrs}
                select -set used */a:ivy_assert */a:ivy_assume */a:ivy_cross_assume
                chformal -remove */a:ivy_property @used %d
                chformal -assert2assume */a:ivy_assume */a:ivy_cross_assume
                chformal -delay 1 */a:ivy_cross_assume
                """
            ).format(
                sby_dir=self.sby_dir,
                engine=shlex.join(self.engine),
                depth=self.depth,
                top=App.config.options.top,
                setattrs=setattrs,
            )
        )
        tl.log_debug(f"wrote sby file {str(self.sby_file)!r}")

        if "sleep" in self.solver_args:  # XXX
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                tl.log_warning("cancelled")
                raise

        self.sby_proc = tl.Process(
            ["sby", "-f", f"{self.filename}.sby"],
            cwd=self.sby_dir.parent,
        )
        self.sby_proc.use_lease = False  # we already hold a job lease

        self.sby_proc.on_exit = lambda returncode: None

        await self.sby_proc.finished

        try:
            sby_status = (self.sby_dir / "status").read_text()
        except FileNotFoundError:
            sby_status = "ERROR"

        sby_status = sby_status.split()[0]

        status_map: dict[str, Status] = {
            "PASS": "pass",
            "FAIL": "fail",
            "UNKNOWN": "unknown",
            "ERROR": "error",
        }

        status = status_map.get(sby_status, "unknown")

        if "unknown" in self.solver_args:  # XXX
            status = "unknown"
        if "fail" in self.solver_args:
            status = "fail"

        try:
            status_lines = (self.sby_dir / sby_status).read_text()
        except FileNotFoundError:
            pass
        else:
            tl.log(status_lines)

        return status
