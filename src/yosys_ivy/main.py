from __future__ import annotations

import io
import json
import os
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path, PurePath
from textwrap import dedent

import click
import yosys_mau.task_loop.job_server as job
from yosys_mau import task_loop as tl
from yosys_mau.source_str import plain_str, report
from yosys_mau.stable_set import StableSet

from .config import App, arg_parser, parse_config
from .data import IvyData, IvyName, IvyProof, IvyTaskName, StatusKey
from .status_db import IvyStatusDb, Status

status_colors = {
    "pending": "blue",
    "scheduled": "cyan",
    "running": "yellow",
    "unknown": "red",
    "error": "red",
    "unreachable": "red",
    "fail": "red",
    "pass": "green",
}


def color_status(status: Status) -> str:
    return click.style(status, fg=status_colors.get(status, None))


def main() -> None:
    args = arg_parser().parse_args()

    job.global_client(args.jobs)

    # Move command line arguments into the App context
    for name in dir(args):
        if name in type(App).__mro__[1].__annotations__:
            setattr(App, name, getattr(args, name))

    App.raw_args = args

    try:
        tl.run_task_loop(task_loop_main)
    except tl.TaskCancelled:
        exit(1)
    except BaseException as e:
        if App.debug or App.debug_events:
            traceback.print_exc()
        tl.log_exception(e, raise_error=False)  # Automatically avoids double logging
        exit(1)


async def task_loop_main() -> None:
    early_log = setup_logging()
    parse_config()
    tl.log_debug("Running command", App.command)

    setup_workdir(setup=App.command in ("run", "setup"), early_log=early_log)

    ivy_export = IvyExportJson()
    await ivy_export.finished
    App.data = ivy_export.data

    if App.command in ("run", "setup"):
        check_proof_cycles()  # TODO always check this?

        create_sby_files()

    run_command()


def setup_logging() -> io.StringIO:
    tl.LogContext.app_name = "IVY"
    tl.logging.start_logging()
    early_log = io.StringIO()
    tl.logging.start_logging(early_log)

    if App.debug_events:
        tl.logging.start_debug_event_logging()
    if App.debug:
        tl.LogContext.level = "debug"

    def error_handler(err: BaseException):
        if isinstance(err, tl.TaskCancelled):
            return
        tl.log_exception(err, raise_error=True)

    tl.current_task().set_error_handler(None, error_handler)
    return early_log


def setup_workdir(early_log: io.StringIO, setup: bool = False) -> None:
    work_dir = App.work_dir

    target = None

    if not setup:
        if not work_dir.exists():
            tl.log_error(
                f"Work directory {str(work_dir)!r} is not initialized, "
                "run the 'setup' command first"
            )

        if App.command != "status":
            counter = 1
            while True:
                try:
                    path = work_dir / f"logfile-{counter}.txt"
                    target = path.open("x")
                except FileExistsError:
                    counter += 1
                else:
                    break

    else:
        try:
            work_dir.mkdir(exist_ok=False)
        except FileExistsError:
            if App.force:
                tl.log(f"Removing existing work directory {str(work_dir)!r}")
                shutil.rmtree(work_dir)
                work_dir.mkdir(exist_ok=False)
            else:
                tl.log_error(
                    f"Work directory {str(work_dir)!r} already exists, use '-f' to overwrite"
                )

        (work_dir / ".gitignore").write_text("*\n")

        if App.command != "status":
            target = (work_dir / "logfile.txt").open("x")

        (work_dir / "model").mkdir()

    if target:
        target.write(early_log.getvalue())
        tl.logging.start_logging(target)
    early_log.close()

    if setup:
        copy_source_files()

    App.status_db = IvyStatusDb(work_dir / "status.sqlite", setup=setup)


def copy_source_files() -> None:
    # TODO factor functionality into mau
    ivy_file_dir = App.ivy_file.parent
    target_src_dir = App.work_dir / "src"

    try:
        target_src_dir.mkdir()
    except FileExistsError:
        tl.log_debug("Reusing existing 'src' directory")
        return

    filenames = App.filenames

    for file in App.config.files:
        path = PurePath(plain_str(file))
        source_file = ivy_file_dir / plain_str(file)
        dst_file = target_src_dir / path.name
        tl.log(f"Copy '{source_file}' to '{dst_file}'")
        if path.parts[-1] in (os.path.pardir, os.path.curdir):
            raise report.InputError(file, "Invalid source file name")

        filename = Path(path.name)

        if filename in filenames:
            raise report.InputError(file, "Duplicate source file name")

        filenames.add(filename)

        # TODO directories
        shutil.copyfile(source_file, dst_file)

    for file, contents in App.config.file.items():
        path = PurePath(plain_str(file))
        dst_file = target_src_dir / plain_str(file)
        tl.log(f"Writing '{file}'")
        if os.path.pardir in path.parts or path.parts[-1] == os.path.curdir:
            raise report.InputError(file, "Invalid file name")

        filename = Path(plain_str(file))
        if filename in filenames:
            raise report.InputError(file, "Duplicate source file name")
        filenames.add(filename)

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        Path(dst_file).write_text(contents)


class IvyExportJson(tl.Process):
    def __init__(self):
        # TODO use common infrastructure for overwriting executable paths
        super().__init__(
            ["yosys", "-ql", "../ivy_export.log", "../ivy_export.ys"], cwd=App.work_dir / "src"
        )
        self.name = "export"
        self.log_output()  # TODO use something that highlights errors/warnings

    async def on_prepare(self) -> None:
        tl.LogContext.scope = self.name

        script = dedent(
            f"""\
                # running in {self.cwd}
                {App.config.read}
                verific -ivy-json-export ../ivy_export.json -top {App.config.options.top}
                verific -assert-all-invariants
                verific -delete-all-proofs
                verific -import {App.config.options.top}
                hierarchy -top {App.config.options.top}
                {App.config.script}
                write_rtlil ../model/export.il
            """
        )
        (App.work_dir / "ivy_export.ys").write_text(script)

        await super().on_prepare()

    async def on_run(self) -> None:
        if (App.work_dir / "ivy_export.json").exists():
            tl.log_debug("Reusing existing 'ivy_export.json'")
            self.on_exit(0)
            return
        await super().on_run()

    def on_exit(self, returncode: int) -> None:
        super().on_exit(returncode)

        with (App.work_dir / "ivy_export.json").open() as f:
            self.data = IvyData(json.load(f))


class IvyPrepareDesign(tl.Process):
    def __init__(self):
        # TODO use common infrastructure for overwriting executable paths
        super().__init__(["yosys", "-ql", "design.log", "design.ys"], cwd=App.work_dir / "model")
        self.name = "design"
        self.log_output()  # TODO use something that highlights errors/warnings

    async def on_prepare(self) -> None:
        tl.LogContext.scope = self.name

        # TODO for future features running the user script to prepare the design just once may not
        # be sufficient, but we should minimize the number of times we run it and still group
        # running it for compatible proof tasks. It would also be possible to reuse the SBY prep
        # step across multiple proof tasks when they share the same solver configuration.

        script = dedent(
            f"""\
                # running in {self.cwd}
                read_rtlil export.il
                {App.config.script}
                write_rtlil design.il
            """
        )
        (App.work_dir / "model" / "design.ys").write_text(script)

    async def on_run(self) -> None:
        if (App.work_dir / "model" / "design.il").exists():
            tl.log_debug("Reusing existing 'design.il'")
            self.on_exit(0)
            return
        await super().on_run()


def check_proof_cycles():
    computed_status = App.data.status_map()

    computed_status.iterate()

    unreachable = [App.data[key.name] for key in computed_status.unreachable_sinks()]

    unreachable.sort(key=lambda entity: (entity.type, entity.name))

    for entity in unreachable:
        # TODO better error message, with cross and multiple proofs, can we do better than
        # listing everything unreachable?
        tl.log_warning(f"unreachable {entity.type} {entity.name}")


def create_sby_files() -> None:
    sby_dir = App.work_dir / "tasks"

    create_files = True

    try:
        sby_dir.mkdir()
    except FileExistsError:
        create_files = False

    if create_files:
        tl.log("Creating proof tasks")

    for task_name in App.data.proof_tasks:
        entity = App.data[task_name.name]

        if not isinstance(entity, IvyProof):
            continue

        setup = entity.step_setup()

        attributes: dict[str, StableSet[IvyName]] = {}

        attributes["ivy_assert"] = setup.asserts
        attributes["ivy_assume"] = setup.assumes
        attributes["ivy_cross_assume"] = setup.cross_assumes

        setattrs = "\n".join(
            f"setattr -set {attr} 1 {' '.join(name.rtlil for name in names)}"
            for attr, names in attributes.items()
            if names
        )

        sby_path = sby_dir / f"{entity.filename}.sby"

        sby_path.write_text(
            dedent(
                """\
                # running in {sby_dir}
                [options]
                mode prove
                depth 20
                assume_early off

                [engines]
                {engines}
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
                sby_dir=sby_dir,
                engines=App.config.engines,
                read=App.config.read,
                top=App.config.options.top,
                setattrs=setattrs,
            )
        )
        tl.log_debug(f"wrote sby file {str(sby_path)!r}")


def run_command() -> None:
    if App.command in ("run", "setup"):
        App.status_db.initialize_status(App.data.proof_tasks)

    if not App.proof_args:
        App.proof_tasks = list(App.data.proof_tasks)
    else:
        tl.log_error("TODO proof task selection not implemented yet")

    status_task = tl.Task(on_run=run_status_task, name="status")

    tl.current_task().sync_handle_events(ProofStatusEvent, proof_event_handler)

    prepare_design = None

    if App.command in ("run", "prove"):
        tasks = App.proof_tasks
        large_task_count = len(tasks) > 10

        if large_task_count:
            tl.log(f"Scheduling {len(tasks)} proof tasks")

        required_status = (
            ("pending", "scheduled", "running") if App.reset_schedule else ("pending",)
        )

        non_pending = App.status_db.change_status_many(tasks, "scheduled", require=required_status)

        for name in tasks:
            if status := non_pending.get(name, None):
                if App.proof_args:
                    # don't warn without an explicit selection of proofs
                    tl.log_warning(f"Status of proof {str(name)!r} is {status!r}, skipping")
            else:
                if not large_task_count:
                    tl.log(f"Scheduling proof task {str(name)!r}")

                proof_task = IvyProofTask(name)
                if prepare_design is None:
                    prepare_design = IvyPrepareDesign()
                proof_task.depends_on(prepare_design)

                proof_task[tl.priority.JobPriorities].priority = (
                    -App.data[name.name].dependency_order(),
                )
                # TODO proof_task[tl.priority.JobPriorities].priority = ...
                # TODO priority sign sentinel task
                proof_task.handle_error(proof_task_error_handler)
                status_task.depends_on(proof_task)


@dataclass
class ProofStatusEvent(tl.TaskEvent):
    name: IvyTaskName
    status: Status


class IvyProofTask(tl.Process):
    def __init__(self, name: IvyTaskName):
        self.filename = App.data[name.name].filename
        super().__init__(
            ["sby", "-f", f"{self.filename}.sby"],
            cwd=App.work_dir / "tasks",
        )
        self.name = f"proof({name})"
        self.proof_name = name
        self[tl.LogContext].scope = str(name.name)

    def on_cancel(self):
        if self.parent and not self.parent.is_aborted:
            tl.log_warning("Cancelled")
        super().on_cancel()

    async def on_run(self) -> None:
        ProofStatusEvent(self.proof_name, "running").emit()
        await super().on_run()

    def on_exit(self, returncode: int) -> None:
        sby_dir = App.work_dir / "tasks" / self.filename
        try:
            sby_status = (sby_dir / "status").read_text()
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

        try:
            status_lines = (sby_dir / sby_status).read_text()
        except FileNotFoundError:
            pass
        else:
            tl.log(status_lines)

        tl.log("Proof status:", color_status(status))
        ProofStatusEvent(self.proof_name, status).emit()


def proof_task_error_handler(err: BaseException):
    if isinstance(err, tl.TaskFailed) and isinstance(err.task, IvyProofTask):
        App.status_db.change_status(err.task.proof_name, "error", require=("running", "scheduled"))
        tl.log_exception(err, raise_error=False)
        # TODO check whether we can de-schedule/abort any proof tasks with this status change
        return
    if isinstance(err, tl.TaskCancelled) and isinstance(err.task, IvyProofTask):
        App.status_db.change_status(
            err.task.proof_name, "pending", require=("running", "scheduled")
        )
        # TODO check whether we can de-schedule/abort any proof tasks with this status change
        return
    if isinstance(err, tl.TaskCancelled):
        return
    tl.log_exception(err)


def proof_event_handler(event: ProofStatusEvent):
    if event.status == "running":
        require = ("scheduled",)
    else:
        require = ("running",)

    failure = App.status_db.change_status(event.name, event.status, require=require)
    if failure:
        tl.log_error(
            "unexpected proof status change",
            event.name,
            event.status,
            failure,
            raise_error=False,
        )
    # TODO check whether we can de-schedule/abort any proof tasks with this status change


def run_status_task():
    if App.command not in ("run", "prove", "status"):
        return
    if not App.proof_tasks:
        tl.log_warning("No proofs selected, no proof status to show")
        return
    tl.log("Proof status:")

    full_status = App.status_db.reduced_status()

    computed_status = App.data.status_map()

    for key in computed_status.status_graph.tasks:
        computed_status.set_status(key, full_status.get(key.name, "pending"))

    computed_status.iterate()

    # TODO produce a nicer status output again
    sink_status = [(App.data[key.name], status) for key, status in computed_status.sink_status()]
    sink_status.sort(key=lambda item: (item[0].type, item[0].name))
    for entity, status in sink_status:
        if isinstance(entity, IvyProof):
            task_status = computed_status.status(StatusKey("task", entity.name))
            if task_status != status:
                tl.log(
                    f"  {entity.prefixed_name}: {color_status(status)}"
                    f" (task {color_status(task_status)})"
                )
            else:
                tl.log(f"  {entity.prefixed_name}: {color_status(status)}")
        else:
            tl.log(f"  {entity.prefixed_name}: {color_status(status)}")
