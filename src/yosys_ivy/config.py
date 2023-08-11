from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

import yosys_mau.config_parser as cfg
from yosys_mau import task_loop as tl
from yosys_mau.source_str import read_file
from yosys_mau.stable_set import StableSet

from .data import IvyData, IvyTaskName
from .status_db import IvyStatusDb


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

    cmd_prove.add_argument(
        "--reset-schedule",
        action="store_true",
        help="assume that no tasks are currently running or scheduled to run",
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
    # TODO split this into multiple contexts and only keep config related parst here
    raw_args: argparse.Namespace

    ivy_file: Path
    work_dir: Path
    force: bool
    debug: bool = False
    debug_events: bool = False

    command: Literal["run", "setup", "prove", "status"]

    proof_args: list[str]
    proof_tasks: list[IvyTaskName]

    reset_schedule: bool = False

    config: IvyConfig

    filenames: StableSet[Path] = StableSet()
    data: IvyData

    status_db: IvyStatusDb


class IvyOptions(cfg.ConfigOptions):
    top = cfg.Option(cfg.StrValue(allow_empty=False))
    default_solver = cfg.Option(cfg.StrValue(), default="sby smtbmc")


class IvyConfig(cfg.ConfigParser):
    options = cfg.OptionsSection(IvyOptions)
    read = cfg.StrSection()

    files = cfg.FilesSection()
    file = cfg.ArgSection(cfg.StrSection(), cfg.StrValue())

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
