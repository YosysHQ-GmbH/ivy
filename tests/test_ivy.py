from dataclasses import dataclass
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import pytest
from yosys_ivy.data import IvyName, IvyTaskName
from yosys_ivy.sccs import find_sccs
from yosys_ivy.status_db import IvyStatusDb, transaction
from yosys_mau.config_parser import split_into_sections
from yosys_mau.source_str import source_map, read_file


def test_find_sccs():
    assert find_sccs({1: [2], 2: [3], 3: [1]}) == [{1, 2, 3}]
    assert find_sccs({1: [2], 2: [3], 3: [2]}) == [{2, 3}, {1}]
    assert find_sccs({1: [2], 2: [3], 3: []}) == [{3}, {2}, {1}]


def test_status_db_retry():
    with tempfile.TemporaryDirectory() as tmpdir:
        status_db_1 = IvyStatusDb(Path(tmpdir) / "status.sqlite", setup=True, timeout=0.1)
        status_db_2 = IvyStatusDb(Path(tmpdir) / "status.sqlite", timeout=0.1)

        task_name = IvyTaskName(IvyName(("test",)), "sby")
        status_db_1.initialize_status([task_name])

        @transaction
        def long_running(status_db: IvyStatusDb, callback: Callable[[], None]):
            status_db.db.execute(
                """ UPDATE proof_status SET status = :status """, dict(status="fail")
            )
            callback()
            status_db.db.execute(
                """ UPDATE proof_status SET status = :status """, dict(status="pass")
            )

        counter = 0

        @transaction
        def other(status_db: IvyStatusDb):
            nonlocal counter
            counter += 1
            if counter == 1:
                status_db.db.execute(
                    """ UPDATE proof_status SET status = :status """, dict(status="scheduled")
                )

        def callback():
            other(status_db_2)

        long_running(status_db_1, callback)

        assert counter == 2


ivy_file_test_dir = Path(__file__).parent / "ivy"

ivy_files = [
    str(path.relative_to(ivy_file_test_dir)) for path in ivy_file_test_dir.rglob("**/*.ivy")
]


@pytest.mark.parametrize("ivy_file", ivy_files)
def test_ivy(ivy_file: str):
    ivy_path = ivy_file_test_dir / ivy_file

    section = next(
        section
        for section in split_into_sections(read_file(ivy_path))
        if section.name == "ivy_self_test"
    )

    span = source_map(section.contents).spans[0]
    line_no = span.file.text_position(span.file_start)[0]
    print(line_no)

    @dataclass
    class IvySelfTest:
        ivy_path: Path

        @property
        def work_dir(self):
            return ivy_path.parent / ivy_path.stem

        def run(self, *args: str) -> int:
            return subprocess.call(
                [
                    sys.executable,
                    Path(__file__).parent.parent / "ivy.py",
                    self.ivy_path.name,
                    *args,
                ],
                cwd=self.ivy_path.parent,
            )

        def capture_status(self) -> str:
            return subprocess.check_output(
                [
                    sys.executable,
                    Path(__file__).parent.parent / "ivy.py",
                    self.ivy_path.name,
                    "status",
                ],
                cwd=self.ivy_path.parent,
                text=True,
            )

        def logfile(self) -> str:
            return (self.work_dir / "logfile.txt").read_text()

    exec(
        compile("\n" * max(0, line_no - 1) + section.contents, ivy_path, "exec"),
        dict(test=IvySelfTest(ivy_path)),
    )
