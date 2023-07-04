from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import traceback
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Any, Callable, Collection, Container, Iterator, Literal

import click
import yosys_mau.config_parser as cfg
from yosys_mau import task_loop as tl
import yosys_mau.task_loop.job_server as job
from yosys_mau.source_str import plain_str, read_file, report
from yosys_mau.stable_set import StableSet

from .sccs import find_sccs


def arg_parser() -> argparse.ArgumentParser:
    global_options = argparse.ArgumentParser(add_help=False)
    usage = "%(prog)s [options] <config>.ivy"
    parser = argparse.ArgumentParser(prog="ivy", usage=usage, parents=[global_options])

    # This is a workaround to have options that can be used before and after the subcommand
    def global_argument(*args: Any, **kwargs: Any):
        global_options.add_argument(
            *args,  # type: ignore
            **{**kwargs, "default": argparse.SUPPRESS},  # type: ignore
        )
        parser.add_argument(*args, **kwargs)

    global_argument(
        "-f", "--force", action="store_true", help="remove the work directory if it already exists"
    )
    global_argument("--debug", action="store_true", help="enable debug logging")
    global_argument("--debug-events", action="store_true", help="enable debug event logging")

    global_argument(
        "-j",
        metavar="<N>",
        type=int,
        dest="jobs",
        help="maximum number of processes to run in parallel",
        default=None,
    )

    parser.add_argument("ivy_file", metavar="<config>.ivy", help=".ivy file", type=Path)

    commands = parser.add_subparsers(
        help="command to run:", required=False, metavar="<command>", dest="command"
    )

    commands.add_parser(
        "setup",
        usage="%(prog)s",
        parents=[global_options],
        help="read SystemVerilog+IVY sources and setup the work directory",
    )

    cmd_run = commands.add_parser(
        "run",
        usage="%(prog)s [proofs...]",
        parents=[global_options],
        help="setup and run all proof tasks (default command)",
    )

    cmd_run.add_argument("proof_args", metavar="<proof>", nargs="*", help="proof tasks to run")

    cmd_prove = commands.add_parser(
        "prove",
        usage="%(prog)s [proofs...]",
        parents=[global_options],
        help="run proof tasks",
    )

    cmd_prove.add_argument("proof_args", metavar="<proof>", nargs="*", help="proof tasks to run")

    cmd_status = commands.add_parser(
        "status",
        usage="%(prog)s [proofs...]",
        parents=[global_options],
        help="show the current proof status",
    )

    cmd_status.add_argument("proof_args", metavar="<proof>", nargs="*", help="proof tasks to run")

    parser.set_defaults(command="run", proof_args=[])

    return parser


@tl.task_context
class App:
    raw_args: argparse.Namespace

    ivy_file: Path
    work_dir: Path
    force: bool
    debug: bool = False
    debug_events: bool = False

    command: Literal["run", "setup", "prove", "status"]

    proof_args: list[str]
    proof_tasks: list[IvyName]

    config: IvyConfig

    filenames: StableSet[Path] = StableSet()
    data: IvyData

    status_db: IvyStatusDb


class IvyOptions(cfg.ConfigOptions):
    top = cfg.Option(cfg.StrValue(allow_empty=False))
    auto_proof = cfg.Option(cfg.BoolValue(), default=True)


class IvyConfig(cfg.ConfigParser):
    options = cfg.OptionsSection(IvyOptions)
    read = cfg.StrSection()

    files = cfg.FilesSection()
    file = cfg.ArgSection(cfg.StrSection(), cfg.StrValue())

    engines = cfg.StrSection(default="smtbmc")
    script = cfg.StrSection(default="prep")


def parse_config() -> None:
    ivy_file = App.ivy_file
    if not ivy_file.name.endswith(".ivy"):
        tl.log_error("An IVY configuration file name has to end with '.ivy'")

    work_dir = ivy_file.with_name(ivy_file.name[:-4])
    App.work_dir = work_dir
    tl.LogContext.work_dir = str(work_dir)

    try:
        App.config = IvyConfig(read_file(App.ivy_file))
    except BaseException:
        tl.log_error("Failed to parse config:", raise_error=False)
        raise


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
        if os.path.pardir in path.parts or path.parts[-1] == os.path.curdir:
            raise report.InputError(file, "Invalid source file name")

        filename = Path(source_file)

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


@dataclass(frozen=True)
class IvyName:
    parts: tuple[str, ...]

    @classmethod
    def from_json(cls, name: list[str]) -> IvyName:
        return cls(tuple(name))

    def local(self, name: str | list[str]) -> IvyName:
        if isinstance(name, list):
            return IvyName(tuple(name))

        return IvyName((*self.parts[:-1], name))

    @property
    def filename(self) -> str:
        joined = ".".join(self.parts)
        return re.sub(r"[^a-zA-Z0-9_.]|^[.]", "_", joined) or "unknown"

    @property
    def instance_names(self) -> tuple[str, ...]:
        return self.parts[1::2]

    @property
    def module_names(self) -> tuple[str, ...]:
        return self.parts[::2]

    def __str__(self) -> str:
        str_parts: list[str] = []
        for part in self.instance_names:
            if re.match(r"^[a-zA-Z0-9_]*$", part):
                str_parts.append(part)
            else:
                str_parts.append(f"\\{part} ")
        return ".".join(str_parts)

    def __repr__(self):
        return str(self)

    @property
    def db_key(self) -> str:
        return json.dumps(self.parts, separators=(",", ":"))

    @classmethod
    def from_db_key(cls, key: str) -> IvyName:
        return cls.from_json(json.loads(key))


def prefix_name(name: IvyName):
    if name in App.data.proofs:
        return f"proof {name}"
    elif name in App.data.invariants:
        return f"invariant {name}"
    else:
        tl.log_warning(f"Unknown name {name}")
        return f"{name}"


Status = Literal["pending", "scheduled", "running", "pass", "fail", "error", "unknown"]


@dataclass
class IvyProof:
    json_data: Any = field(repr=False)

    name: IvyName
    top_level: bool
    src_loc: str
    use_proof: StableSet[IvyName]
    use_invariant: StableSet[IvyName]
    assert_invariant: StableSet[IvyName]

    filename: str

    def __init__(self, json_data: Any):
        self.json_data = json_data

        self.name = IvyName(tuple(json_data["name"]))
        self.top_level = self.json_data["top_level"]
        self.src_loc = self.json_data["srcloc"]
        self.use_proof = StableSet(self.name.local(name) for name in self.json_data["use_proof"])
        self.use_invariant = StableSet(
            self.name.local(name) for name in self.json_data["use_invariant"]
        )
        self.assert_invariant = StableSet(
            self.name.local(name) for name in self.json_data["assert_invariant"]
        )

    def edges(self) -> Collection[IvyName]:
        return self.use_proof | self.use_invariant

    def assertions(self) -> Collection[IvyName]:
        return self.assert_invariant


@dataclass
class IvyInvariant:
    json_data: Any = field(repr=False)

    name: IvyName
    src_loc: str

    filename: str

    asserted_by: StableSet[IvyName]
    used_by: StableSet[IvyName]

    def __init__(self, json_data: Any):
        self.json_data = json_data

        self.name = IvyName(tuple(json_data["name"]))
        self.src_loc = self.json_data["srcloc"]

        self.asserted_by = StableSet()
        self.used_by = StableSet()

    def edges(self) -> Collection[IvyName]:
        return self.asserted_by

    def assertions(self) -> Collection[IvyName]:
        return ()


@dataclass
class IvyData:
    json_data: Any = field(repr=False)

    proofs: dict[IvyName, IvyProof]
    invariants: dict[IvyName, IvyInvariant]

    filenames: set[str] = field(repr=False)

    sccs: list[StableSet[IvyName]] = field(repr=False)

    def __init__(self, json_data: Any):
        self.json_data = json_data

        self.proofs = {}
        self.invariants = {}

        self.filenames = set()

        for proof_data in json_data["proofs"]:
            proof = IvyProof(proof_data)
            proof.filename = self.uniquify(proof.name.filename)
            self.proofs[proof.name] = proof

        for invariant_data in json_data["invariants"]:
            invariant = IvyInvariant(invariant_data)
            invariant.filename = self.uniquify(invariant.name.filename)
            self.invariants[invariant.name] = invariant

        for proof in self.proofs.values():
            for invariant_name in proof.assert_invariant:
                self.invariants[invariant_name].asserted_by.add(proof.name)
            for invariant_name in proof.use_invariant:
                self.invariants[invariant_name].used_by.add(proof.name)

        self.sccs = find_sccs(self.proof_graph())

    def uniquify(self, name: str) -> str:
        if name not in self.filenames:
            self.filenames.add(name)
            return name
        i = 1
        while (candidate := f"{name}_{i}") in self.filenames:
            i += 1
        self.filenames.add(candidate)
        return candidate

    def __getitem__(self, name: IvyName):
        try:
            return self.proofs[name]
        except KeyError:
            pass
        return self.invariants[name]

    def __iter__(self) -> Iterator[IvyName]:
        yield from self.proofs
        yield from self.invariants

    def proof_graph(self):
        return {name: self[name].edges() for name in self}

    def proof_cycles(self):
        return list(filter(lambda component: len(component) > 1, self.sccs))

    def check_proof_cycles(self):
        cycles = self.proof_cycles()

        if cycles:
            message: list[str] = []

            for scc in cycles:
                message.append("The following proofs and invariants have cyclic dependencies:")

                for name in scc:
                    item = self[name]
                    if isinstance(item, IvyProof):
                        message.append(f"  proof {item.name} ({item.src_loc})")
                        for other_name in item.use_proof & scc:
                            other = self[other_name]
                            message.append(f"    uses proof {other.name} ({other.src_loc})")
                        for other_name in item.use_invariant & scc:
                            other = self[other_name]
                            message.append(f"    uses invariant {other.name} ({other.src_loc})")
                    else:
                        message.append(f"  invariant {item.name} ({item.src_loc})")
                        for other_name in item.asserted_by & scc:
                            other = self[other_name]
                            message.append(f"    asserted by proof {other.name} ({other.src_loc})")

            tl.log_error("\n".join(message))

    def topological_order(self):
        return [next(iter(component)) for component in self.sccs]

    def recursive_uses(
        self, name: IvyName, output: StableSet[IvyName] | None = None
    ) -> StableSet[IvyName]:
        if output is None:
            output = StableSet()
        if name in output:
            return output
        output.add(name)

        if name in self.proofs:
            for dep in self.proofs[name].edges():
                self.recursive_uses(dep, output)
        return output


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

    for name in App.data:
        item = App.data[name]
        if isinstance(item, IvyInvariant):
            if item.asserted_by:
                continue

            if not App.config.options.auto_proof:
                unprovable.add(name)
                continue

        proof_tasks.add(name)

        if not create_files:
            continue

        uses = App.data.recursive_uses(name)

        placeholder_defines: list[str] = []

        for use in uses:
            if use in App.data.proofs:
                continue
            macro_name = str(use).replace(".", "__")
            placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assume")

        if isinstance(item, IvyInvariant):
            macro_name = str(name).replace(".", "__")
            placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assert")
        else:
            for assertion in item.assertions():
                macro_name = str(assertion).replace(".", "__")
                placeholder_defines.append(f"verific -vlog-define inv_{macro_name}=assert")

        sby_path = sby_dir / f"{item.filename}.sby"

        sby_path.write_text(
            dedent(
                """\
                # running in {sby_dir}
                [options]
                mode prove
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


status_colors = {
    "pending": "blue",
    "scheduled": "cyan",
    "running": "yellow",
    "unknown": "red",
    "error": "red",
    "fail": "red",
    "pass": "green",
}


def color_status(status: Status) -> str:
    return click.style(status, fg=status_colors.get(status, None))


async def task_loop_main() -> None:
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

    parse_config()

    tl.log_debug("Running command", App.command)

    setup_workdir(setup=App.command in ("run", "setup"), early_log=early_log)

    ivy_export = IvyExportJson()
    await ivy_export.finished
    App.data = ivy_export.data

    App.data.check_proof_cycles()  # TODO always check this?

    proof_tasks = create_sby_files()

    if App.command in ("run", "setup"):
        App.status_db.initialize_status(proof_tasks)

    if not App.proof_args:
        App.proof_tasks = list(proof_tasks)
    else:
        proofs: list[IvyName] = []
        for arg in App.proof_args:
            found = None
            # TODO better matching
            for name in proof_tasks:
                if name.parts[-1] == arg:
                    found = name
                    break
            if found is None:
                tl.log_error(f"Proof task {arg!r} not found")
            else:
                proofs.append(found)

        App.proof_tasks = proofs

    def run_status_task():
        if App.command not in ("run", "prove", "status"):
            return
        if not App.proof_tasks:
            tl.log_warning("No proofs selected, no proof status to show")
            return
        tl.log("Proof status:")

        full_status = App.status_db.full_status()

        dep_status: dict[IvyName, Status] = {}

        for name in App.data.topological_order():
            item = App.data[name]
            incoming: list[Status] = []

            if name in full_status:
                incoming.append(full_status[name])

            for edge in item.edges():
                incoming.append(dep_status[edge])

            if not incoming:
                dep_status[name] = "unknown"
                continue

            priority = ["error", "fail", "unknown", "running", "scheduled", "pending", "pass"]

            for status in priority:
                if status in incoming:
                    dep_status[name] = status
                    break

        for name in App.proof_tasks:
            dep = dep_status[name]
            step = full_status[name]
            src_loc = App.data[name].src_loc

            tl.log(f"  {prefix_name(name)}: {color_status(dep)} ({src_loc})")
            for edge in App.data[name].edges():
                src_loc = App.data[edge].src_loc
                tl.log(f"    use {prefix_name(edge)}: {color_status(dep_status[edge])} ({src_loc})")

            if name in App.data.proofs:
                tl.log(f"    proof step: {color_status(step)}")

                for edge in App.data[name].assertions():
                    src_loc = App.data[edge].src_loc
                    tl.log(
                        f"    assert {prefix_name(edge)}: "
                        f"{color_status(dep_status[edge])} ({src_loc})"
                    )

    status_task = tl.Task(on_run=run_status_task, name="status")

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
                proof_task.handle_error(proof_task_error_handler)
                status_task.depends_on(proof_task)


def main() -> None:
    args = arg_parser().parse_args()

    job.global_client(args.jobs)

    for name in dir(args):
        if name in type(App).__annotations__:
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
