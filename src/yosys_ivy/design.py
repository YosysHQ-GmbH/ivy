from __future__ import annotations

from yosys_mau import task_loop as tl

from .config import App


class PrepareDesign(tl.Process):
    def __init__(self):
        # TODO use common infrastructure for overwriting executable paths
        super().__init__(["yosys", "-ql", "design.log", "design.ys"], cwd=App.work_dir / "model")
        self.name = "prepare"
        self.log_output()  # TODO use something that highlights errors/warnings

    async def on_prepare(self) -> None:
        # TODO for future features running the user script to prepare the design just once may not
        # be sufficient, but we should minimize the number of times we run it and still group
        # running it for compatible proof tasks. It would also be possible to reuse the SBY prep
        # step across multiple proof tasks when they share the same solver configuration.

        script = "\n".join(
            [
                f"# running in {self.cwd}",
                "read_rtlil export.il",
                App.config.script,
                "write_rtlil design.il",
                "",
            ]
        )
        (App.work_dir / "model" / "design.ys").write_text(script)

    async def on_run(self) -> None:
        if (App.work_dir / "model" / "design.il").exists():
            tl.log_debug("Reusing existing 'design.il'")
            self.on_exit(0)
            return
        await super().on_run()


class Design(tl.TaskGroup):
    _prepare_design: PrepareDesign | None

    def __init__(self):
        super().__init__(name="design")
        self._prepare_design = None

        with self.as_current_task():
            tl.LogContext.scope = "design"

        DesignContext.design = self

    def prepare_design(self) -> PrepareDesign:
        if self._prepare_design is None:
            with self.as_current_task():
                self._prepare_design = PrepareDesign()
        return self._prepare_design


@tl.task_context
class DesignContext:
    design: Design
