from __future__ import annotations

import io
import json
import os
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Collection

import click
import yosys_mau.task_loop.job_server as job
from yosys_mau import task_loop as tl
from yosys_mau.source_str import plain_str, report
from yosys_mau.stable_set import StableSet

from .config import App, arg_parser, parse_config
from .data import IvyData, IvyName, IvyProof, StatusKey
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

    check_proof_cycles()  # TODO always check this?

    all_proof_tasks = create_sby_files()

    run_command(all_proof_tasks)


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


def check_proof_cycles():
    computed_status = App.data.status_map()

    # TODO how does this interact with the 'solve' statement in the input?

    for entity in App.data.proofs:
        computed_status.set_status(StatusKey("step", entity.name), "pass")
    computed_status.iterate()

    for entity in App.data:
        if computed_status.status(StatusKey("entity", entity.name)) == "unreachable":
            # TODO better error message, with cross and multiple proofs, can we do better than
            # listing everything unreachable?
            tl.log_error("unreachable")


def create_sby_files() -> Collection[IvyName]:
    sby_dir = App.work_dir / "tasks"

    create_files = True

    try:
        sby_dir.mkdir()
    except FileExistsError:
        create_files = False

    relative = Path(os.path.pardir) / "src"

    files = "\n".join(str(relative / name) for name in App.filenames)

    proof_tasks = StableSet()

    unprovable = StableSet()

    for item in App.data.proofs:
        proof_tasks.add(item.name)

        if not create_files:
            continue

        setup = item.step_setup()

        placeholder_defines: list[str] = []

        for assert_name in setup.asserts:
            macro_name = str(assert_name.parts[-1])
            placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assert")
            placeholder_defines.append(f"verific -vlog-define cross_{macro_name}=")

        for assume_name in setup.assumes:
            macro_name = str(assume_name.parts[-1])
            placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assume")
            placeholder_defines.append(f"verific -vlog-define cross_{macro_name}=")

        for assume_name in setup.cross_assumes:
            macro_name = str(assume_name.parts[-1])
            placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assume")
            placeholder_defines.append(
                f"verific -vlog-define cross_{macro_name}=$initstate || $past"
            )

        sby_path = sby_dir / f"{item.filename}.sby"

        sby_path.write_text(
            dedent(
                """\
                # running in {sby_dir}
                [options]
                mode prove
                depth 2

                [engines]
                {engines}
                [script]
                {placeholder_defines}
                {read}
                verific -delete-all-proofs
                verific -delete-all-invariants

                hierarchy -top {top}
                {script}
                [files]
                {files}
                """
            ).format(
                placeholder_defines="\n".join(placeholder_defines),
                sby_dir=sby_dir,
                engines=App.config.engines,
                read=App.config.read,
                files=files,
                top=App.config.options.top,
                script=App.config.script,
            )
        )
        tl.log_debug(f"wrote sby file {str(sby_path)!r}")

    if unprovable:
        message = "\n".join(f"  {name} ({App.data[name].src_loc})" for name in unprovable)
        tl.log_error(
            f"Invariants without proof:\n{message}\n"
            "Use option 'auto_proof on' to add implicit proofs for these invariants."
        )

    return proof_tasks


def run_command(all_proof_tasks: Collection[IvyName]) -> None:
    if App.command in ("run", "setup"):
        App.status_db.initialize_status(all_proof_tasks)

    if not App.proof_args:
        App.proof_tasks = list(all_proof_tasks)
    else:
        proofs: list[IvyName] = []
        for arg in App.proof_args:
            found = None
            # TODO better matching, move into config.py
            for name in all_proof_tasks:
                if name.parts[-1] == arg:
                    found = name
                    break
            if found is None:
                tl.log_error(f"Proof task {arg!r} not found")
            else:
                proofs.append(found)

        App.proof_tasks = proofs

    status_task = tl.Task(on_run=run_status_task, name="status")

    tl.current_task().sync_handle_events(ProofStatusEvent, proof_event_handler)

    if App.command in ("run", "prove"):
        for name in App.proof_tasks:
            if status := App.status_db.change_status(name, "scheduled", require=("pending",)):
                if App.proof_args:
                    # don't warn without an explicit selection of proofs
                    tl.log_warning(f"Status of proof {str(name)!r} is {status!r}, skipping")
            else:
                tl.log(f"Scheduling proof task {str(name)!r}")

                proof_task = IvyProofTask(name)
                # TODO proof_task[tl.priority.JobPriorities].priority = ...
                # TODO priority sign sentinel task
                proof_task.handle_error(proof_task_error_handler)
                status_task.depends_on(proof_task)


@dataclass
class ProofStatusEvent(tl.TaskEvent):
    name: IvyName
    status: Status


class IvyProofTask(tl.Process):
    def __init__(self, name: IvyName):
        self.filename = App.data[name].filename
        super().__init__(
            ["sby", "-f", f"{self.filename}.sby"],
            cwd=App.work_dir / "tasks",
        )
        self.name = f"proof({name})"
        self.proof_name = name
        self[tl.LogContext].scope = str(name)

    def on_cancel(self):
        if self.parent and not self.parent.is_aborted:
            tl.log_warning("Cancelled")
        super().on_cancel()

    async def on_run(self) -> None:
        ProofStatusEvent(self.proof_name, "running").emit()
        await super().on_run()

    def on_exit(self, returncode: int) -> None:
        print("on_exit_called")
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
        App.status_db.change_status(
            err.task.proof_name, "unknown", require=("running", "scheduled")
        )
        tl.log_exception(err, raise_error=False)
        return
    if isinstance(err, tl.TaskCancelled) and isinstance(err.task, IvyProofTask):
        App.status_db.change_status(
            err.task.proof_name, "pending", require=("running", "scheduled")
        )
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


def run_status_task():
    if App.command not in ("run", "prove", "status"):
        return
    if not App.proof_tasks:
        tl.log_warning("No proofs selected, no proof status to show")
        return
    tl.log("Proof status:")

    full_status = App.status_db.full_status()

    computed_status = App.data.status_map()

    for name, status in full_status.items():
        computed_status.set_status(StatusKey("step", name), status)

    computed_status.iterate()

    # TODO produce a nicer status output again
    for entity in App.data:
        status = computed_status.status(
            StatusKey("proof" if isinstance(entity, IvyProof) else "entity", entity.name)
        )
        tl.log(f"  {entity.prefixed_name}: {color_status(status)}")
