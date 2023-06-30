from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Any, Collection, Iterator

import yosys_mau.config_parser as cfg
from yosys_mau import task_loop as tl
from yosys_mau.source_str import plain_str, read_file, report
from yosys_mau.stable_set import StableSet

from .sccs import find_sccs


def arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    parser = argparse.ArgumentParser(prog="ivy", usage="%(prog)s [options] <config>.ivy")

    # TODO workdir
    parser.add_argument(
        "-f", "--force", action="store_true", help="remove workdir if it already exists"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--debug-events", action="store_true", help="Enable debug event logging")

    parser.add_argument("ivy_file", metavar="<config>.ivy", help=".ivy file", type=Path)

    return parser


@tl.task_context
class App:
    raw_args: argparse.Namespace

    ivy_file: Path
    work_dir: Path
    force: bool
    debug: bool = False
    debug_events: bool = False

    config: IvyConfig

    data: IvyData


class IvyOptions(cfg.ConfigOptions):
    top = cfg.Option(cfg.StrValue(allow_empty=False))


class IvyConfig(cfg.ConfigParser):
    options = cfg.OptionsSection(IvyOptions)
    read = cfg.StrSection()

    files = cfg.FilesSection()
    file = cfg.ArgSection(cfg.StrSection(), cfg.StrValue())


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

    for file in App.config.files:
        path = PurePath(plain_str(file))
        source_file = ivy_file_dir / plain_str(file)
        dst_file = target_src_dir / path.name
        tl.log(f"Copy '{source_file}' to '{dst_file}'")
        if os.path.pardir in path.parts or path.parts[-1] == os.path.curdir:
            raise report.InputError(file, "Invalid source file name")

        # TODO directories
        shutil.copyfile(source_file, dst_file)

    for file, contents in App.config.file.items():
        path = PurePath(plain_str(file))
        dst_file = target_src_dir / plain_str(file)
        tl.log(f"Writing '{file}'")
        if os.path.pardir in path.parts or path.parts[-1] == os.path.curdir:
            raise report.InputError(file, "Invalid file name")
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


@dataclass
class IvyInvariant:
    json_data: Any = field(repr=False)

    name: IvyName
    src_loc: str

    filename: str

    asserted_by: StableSet[IvyName]

    def __init__(self, json_data: Any):
        self.json_data = json_data

        self.name = IvyName(tuple(json_data["name"]))
        self.src_loc = self.json_data["srcloc"]

        self.asserted_by = StableSet()

    def edges(self) -> Collection[IvyName]:
        return self.asserted_by


@dataclass
class IvyData:
    json_data: Any = field(repr=False)

    proofs: dict[IvyName, IvyProof]
    invariants: dict[IvyName, IvyInvariant]

    filenames: set[str] = field(repr=False)

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
        return list(filter(lambda component: len(component) > 1, find_sccs(self.proof_graph())))

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


def setup_workdir(reuse: bool = False) -> None:
    work_dir = App.work_dir

    if reuse:
        if not work_dir.exists():
            # TODO more helpful error message when different commands are implemented
            tl.log_error(f"Work directory {str(work_dir)!r} is not initialized")
        return

    try:
        work_dir.mkdir(exist_ok=False)
    except FileExistsError:
        if App.force:
            tl.log(f"Removing existing work directory {str(work_dir)!r}")
            shutil.rmtree(work_dir)
            work_dir.mkdir(exist_ok=False)
        else:
            tl.log_error(f"Work directory {str(work_dir)!r} already exists, use '-f' to overwrite")

    (work_dir / ".gitignore").write_text("*\n")

    copy_source_files()


async def task_loop_main() -> None:
    tl.LogContext.app_name = "IVY"
    tl.logging.start_logging()

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

    # TODO handle different commands (setup/prove/status)

    setup_workdir(reuse=False)  # TODO set reuse according to the current command

    ivy_export = IvyExportJson()
    await ivy_export.finished
    App.data = ivy_export.data

    App.data.check_proof_cycles()

    tl.log_warning("not fully implemented yet")


def main() -> None:
    args = arg_parser().parse_args()

    for name in dir(args):
        if name.startswith("_"):
            continue
        setattr(App, name, getattr(args, name))

    App.raw_args = args

    try:
        tl.run_task_loop(task_loop_main)
    except BaseException as e:
        if App.debug or App.debug_events:
            traceback.print_exc()
        tl.log_exception(e, raise_error=False)  # Automatically avoids double logging
        exit(1)
