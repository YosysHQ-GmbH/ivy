from __future__ import annotations

import io
import json
import os
import pickle
import shutil
import traceback
from pathlib import Path, PurePath

import yosys_mau.task_loop.job_server as job
from yosys_mau import task_loop as tl
from yosys_mau.source_str import plain_str, report

from yosys_ivy.design import Design

from .config import App, arg_parser, parse_config
from .data import IvyData, IvyProof, StatusKey, color_status
from .solver import ProofStatusEvent, SolverContext, Solvers
from .status_db import IvyStatusDb


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

    Design()
    Solvers()

    if App.command in ("run", "setup"):
        check_proof_cycles()  # TODO always check this?

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
        (work_dir / "tasks").mkdir()

    if App.config.ivy_self_test:
        (work_dir / "self_test").write_text(App.config.ivy_self_test)

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

    target_src_dir.mkdir()

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

        script = "\n".join(
            [
                f"# running in {self.cwd}",
                App.config.read,
                f"verific -static -top {App.config.options.top}",
                "verific -unroll",
                f"verific -ivy-json-export ../ivy_export.json -top {App.config.options.top}",
                "verific -assert-all-invariants",
                "verific -delete-all-proofs",
                f"verific -import {App.config.options.top}",
                f"hierarchy -top {App.config.options.top}",
                App.config.script,
                "write_rtlil ../model/export.il",
                "",
            ]
        )
        (App.work_dir / "ivy_export.ys").write_text(script)

        await super().on_prepare()

    async def on_run(self) -> None:
        try:
            pickle_file = (App.work_dir / "ivy_data.pickle").open("rb")
        except FileNotFoundError:
            pass
        else:
            tl.log_debug("Reusing existing 'ivy_data.pickle'")
            with pickle_file:
                self.data = pickle.load(pickle_file)
            return

        if (App.work_dir / "ivy_export.json").exists():
            tl.log_debug("Reusing existing 'ivy_export.json'")
            self.on_exit(0)
            return
        await super().on_run()

    def on_exit(self, returncode: int) -> None:
        super().on_exit(returncode)

        with (App.work_dir / "ivy_export.json").open() as f:
            self.data = IvyData(json.load(f))
        with (App.work_dir / "ivy_data.pickle").open("wb") as f:
            pickle.dump(self.data, f)


def check_proof_cycles():
    computed_status = App.data.status_map()

    computed_status.iterate()

    unreachable = [App.data[key.name] for key in computed_status.unreachable_sinks()]

    unreachable.sort(key=lambda entity: (entity.type, entity.name))

    for entity in unreachable:
        # TODO better error message, with cross and multiple proofs, can we do better than
        # listing everything unreachable?
        tl.log_warning(f"unreachable {entity.type} {entity.name}")


def run_command() -> None:
    if App.command in ("run", "setup"):
        App.status_db.initialize_status(App.data.proof_tasks)

    if not App.proof_args:
        App.proof_tasks = list(App.data.proof_tasks)
    else:
        tl.log_error("TODO proof task selection not implemented yet")

    status_task = tl.Task(on_run=run_status_task, name="status")

    tl.current_task().sync_handle_events(ProofStatusEvent, proof_event_handler)

    if App.command in ("run", "prove"):
        tasks = App.proof_tasks
        large_task_count = len(tasks) > 10

        if large_task_count:
            tl.log(f"Scheduling {len(tasks)} proof tasks")

        required_status = (
            ("pending", "scheduled", "running") if App.reset_schedule else ("pending",)
        )

        non_pending = App.status_db.change_status_many(tasks, "scheduled", require=required_status)

        for task in tasks:
            info = App.data.task_info[task]
            if status := non_pending.get(task, None):
                if App.proof_args:
                    # don't warn without an explicit selection of proofs
                    tl.log_warning(f"Status of proof {info} is {status!r}, skipping")
            else:
                if not large_task_count:
                    tl.log(f"Scheduling proof task {info}")

                SolverContext.solvers.dispatch_proof_task(task)

    status_task.depends_on(SolverContext.solvers)


def proof_event_handler(event: ProofStatusEvent):
    if event.status in ("abandoned", "pending"):
        require = ("pending", "scheduled", "running")
    elif event.status == "running":
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

    if event.status in ("pass", "fail"):
        SolverContext.solvers.cancel_proof_tasks(
            event.name.name, already_solved=True, abandoned=False
        )

    if event.status in ("pass", "fail"):
        status_ticks = App.status_db.status_ticks

        async def background():
            check_useless_tasks(status_ticks)

        tl.current_task().background(background)
    # TODO check whether we can de-schedule/abort any proof tasks with this status change


def check_useless_tasks(status_ticks: int):
    if App.status_db.status_ticks > status_ticks:
        return
    App.status_db.status_ticks += 1

    # TODO debounce this so when many tasks finish quickly we don't spend too much time recomputing
    # the status graph again and again
    full_status = App.status_db.reduced_status()

    computed_status = App.data.status_map()

    for key in computed_status.status_graph.tasks:
        computed_status.set_status(key, full_status.get(key.name, "pending"))

    computed_status.iterate()

    computed_status.mark_sinks_as_useful()

    computed_status.iterate_useful()

    for task in computed_status.status_graph.tasks:
        if not computed_status.useful(task) and computed_status.status(task) in (
            "pending",
            "scheduled",
            "running",
        ):
            SolverContext.solvers.cancel_proof_tasks(
                task.name, already_solved=False, abandoned=True
            )


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
