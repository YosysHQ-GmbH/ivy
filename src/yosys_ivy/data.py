from __future__ import annotations

import abc
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, NamedTuple

import yosys_mau.task_loop as tl
from yosys_mau.stable_set import StableSet

Status = Literal[
    "pending", "scheduled", "running", "pass", "fail", "error", "unknown", "unreachable"
]

NodeType = Literal["and", "or"]


status_list: list[Status] = [
    "unreachable",
    "error",
    "fail",
    "unknown",
    "pending",
    "scheduled",
    "running",
    "pass",
]


status_index = {status: i for i, status in enumerate(status_list)}


def status_and(*status: Status) -> Status:
    return status_list[min((status_index[s] for s in status), default=len(status_list) - 1)]


def status_or(*status: Status) -> Status:
    return status_list[max((status_index[s] for s in status), default=0)]


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


StatusType = Literal[
    "step",  # The result of running a proof, assuming all assumptions hold
    "proof",  # The result of the proof and its assumptions
    "assume_proof",  # The conjunction of all non-locally asserted assumptions of a proof
    "entity",  # The status of an entity that is assumed
    "cross",  # The status of an entity that is cross-assumed
    "export",  # The status of everything exported by an entity
]


StatusKey = NamedTuple("StatusKey", [("type", StatusType), ("name", IvyName)])


@dataclass(frozen=True)
class StatusEdge:
    source: StatusKey
    target: StatusKey


IvyType = Literal["invariant", "proof", "sequence", "property"]


@dataclass
class IvyEntity(abc.ABC):
    ivy_data: IvyData = field(repr=False)
    json_data: Any = field(repr=False)
    type: IvyType

    name: IvyName
    filename: str
    src_loc: str

    def __init__(self, ivy_data: IvyData, json_data: Any):
        self.ivy_data = ivy_data
        self.json_data = json_data
        self.name = IvyName(tuple(json_data["name"]))
        self.src_loc = self.json_data["srcloc"]
        self.filename = ivy_data.uniquify(self.name.filename)
        if self.name in ivy_data.entities:
            tl.log_error("name collision in export data")  # TODO better error message
        ivy_data.entities[self.name] = self

    def edges(self) -> Iterator[StatusEdge]:
        yield from ()

    @property
    def prefixed_name(self) -> str:
        return f"{self.type} {self.name}"


@dataclass(frozen=True)
class IvyProofItem:
    name: IvyName

    def __init__(self, proof: IvyProof, json_data: Any):
        object.__setattr__(self, "name", proof.name.local(json_data["name"]))
        if self in proof.items:
            tl.log_error("duplicate item in proof")  # TODO better error message
        proof.items.add(self)


@dataclass(frozen=True)
class IvyExport(IvyProofItem):
    type: IvyType
    name: IvyName
    cross: bool

    def __init__(self, proof: IvyProof, json_data: Any):
        type = json_data["type"]
        assert type in ("invariant", "proof", "sequence", "property")
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "cross", json_data.get("cross", False))
        super().__init__(proof, json_data)
        proof.exports.add(self)


@dataclass(frozen=True)
class IvyUse(IvyProofItem):
    export: bool

    def __init__(self, proof: IvyProof, json_data: Any):
        object.__setattr__(self, "export", json_data.get("export", False))
        super().__init__(proof, json_data)
        proof.uses.add(self)


@dataclass(frozen=True)
class IvyAssume(IvyProofItem):
    type: IvyType
    cross: bool

    def __init__(self, proof: IvyProof, json_data: Any):
        type = json_data["type"]
        assert type in ("invariant", "proof", "sequence", "property")
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "cross", json_data.get("cross", False))
        super().__init__(proof, json_data)
        proof.assumes.add(self)


@dataclass(frozen=True)
class IvyAssert(IvyProofItem):
    local: bool

    def __init__(self, proof: IvyProof, json_data: Any):
        assert json_data.get("type", "invariant") == "invariant"
        object.__setattr__(self, "local", json_data.get("local", False))
        super().__init__(proof, json_data)
        proof.asserts.add(self)

    def as_assume(self, cross: bool):
        assume = object.__new__(IvyAssume)
        object.__setattr__(assume, "name", self.name)
        object.__setattr__(assume, "export", False)
        object.__setattr__(assume, "type", "invariant")
        object.__setattr__(assume, "cross", cross)
        return assume


@dataclass
class StepSetup:
    assumes: StableSet[IvyName]
    cross_assumes: StableSet[IvyName]
    asserts: StableSet[IvyName]


@dataclass
class IvyProof(IvyEntity):
    type = "proof"
    top_level: bool
    automatic: bool

    items: StableSet[IvyProofItem]

    uses: StableSet[IvyUse]
    assumes: StableSet[IvyAssume]
    asserts: StableSet[IvyAssert]
    exports: StableSet[IvyExport]

    # exports: list[tuple[IvyName]]
    # use_invariant: StableSet[IvyName]
    # assert_invariant: StableSet[IvyName]

    def __init__(self, ivy_data: IvyData, json_data: Any):
        super().__init__(ivy_data, json_data)

        self.top_level = self.json_data.get("top_level", False)
        self.automatic = self.json_data.get("automatic", False)

        self.items = StableSet()
        self.uses = StableSet()
        self.exports = StableSet()
        self.asserts = StableSet()
        self.assumes = StableSet()

        for use_proof_json in self.json_data.get("use_proof", []):
            IvyUse(self, use_proof_json)

        for assume_json in self.json_data.get("assume", []):
            IvyAssume(self, assume_json)
            if assume_json.get("export", False):  # TODO temporary, replace when JSON is updated
                IvyExport(self, assume_json)

        for assert_json in self.json_data.get("assert", []):
            IvyAssert(self, assert_json)
            if assert_json.get("export", False):  # TODO temporary, replace when JSON is updated
                IvyExport(self, assert_json)

        ivy_data.proofs.append(self)

    def edges(self) -> Iterator[StatusEdge]:
        yield StatusEdge(StatusKey("step", self.name), StatusKey("proof", self.name))

        for assume_item in self.assumes:
            yield StatusEdge(
                StatusKey("cross" if assume_item.cross else "entity", assume_item.name),
                StatusKey("proof", self.name),
            )

        for use_item in self.uses:
            yield StatusEdge(
                StatusKey("export", use_item.name),
                StatusKey("proof", self.name),
            )

            if use_item.export:
                yield StatusEdge(
                    StatusKey("export", use_item.name),
                    StatusKey("export", self.name),
                )

        for assert_item in self.asserts:
            if not assert_item.local:  # assuming a proof doesn't assume local properties
                yield StatusEdge(
                    StatusKey("entity", assert_item.name),
                    StatusKey("assume_proof", self.name),
                )

            yield StatusEdge(
                StatusKey("proof", self.name),
                StatusKey("entity", assert_item.name),
            )

        # We have an extra node because we want the and of all the assumptions but entity nodes take
        # the or of all their incoming edges.
        yield StatusEdge(StatusKey("assume_proof", self.name), StatusKey("entity", self.name))

        for export_item in self.exports:
            yield StatusEdge(
                StatusKey("cross" if export_item.cross else "entity", export_item.name),
                StatusKey("export", self.name),
            )

    def step_setup(self) -> StepSetup:
        assumes = StableSet()
        cross_assumes = StableSet()
        asserts = StableSet()

        for assert_item in self.asserts:
            asserts.add(assert_item.name)

        imported = StableSet()
        pending_imports: list[IvyName] = []

        for assume_item in self.assumes:
            target = cross_assumes if assume_item.cross else assumes
            assumed_entity = self.ivy_data[assume_item.name]
            if isinstance(assumed_entity, IvyProof):
                for assert_item in assumed_entity.asserts:
                    if not assert_item.local:
                        target.add(assert_item.name)
            else:
                target.add(assume_item.name)

        for use_item in self.uses:
            if use_item.name not in imported:
                imported.add(use_item.name)
                pending_imports.append(use_item.name)

        while pending_imports:
            import_name = pending_imports.pop()
            import_entity = self.ivy_data[import_name]
            assert isinstance(import_entity, IvyProof)  # Should have been checked by the frontend

            for export_item in import_entity.exports:
                target = cross_assumes if export_item.cross else assumes
                exported_entity = self.ivy_data[export_item.name]
                if isinstance(exported_entity, IvyProof):
                    for assert_item in exported_entity.asserts:
                        if not assert_item.local:
                            target.add(assert_item.name)
                else:
                    target.add(export_item.name)

            for use_item in import_entity.uses:
                if use_item.export and use_item.name not in imported:
                    imported.add(use_item.name)
                    pending_imports.append(use_item.name)

        return StepSetup(
            assumes=assumes,
            cross_assumes=cross_assumes,
            asserts=asserts,
        )


@dataclass
class IvyInvariant(IvyEntity):
    type = "invariant"

    def __init__(self, ivy_data: IvyData, json_data: Any):
        super().__init__(ivy_data, json_data)
        ivy_data.invariants.append(self)

    def combine_status(self, *status: Status) -> Status:
        return status_or(*status)


@dataclass
class IvyData:
    json_data: Any = field(repr=False)

    proofs: list[IvyProof]
    invariants: list[IvyInvariant]

    entities: dict[IvyName, IvyEntity]

    filenames: set[str] = field(repr=False)

    status_out_edges: dict[StatusKey, StableSet[StatusEdge]]
    status_in_edges: dict[StatusKey, StableSet[StatusEdge]]

    status_non_entity_sources: StableSet[StatusKey]

    def __init__(self, json_data: Any):
        self.json_data = json_data

        self.proofs = []
        self.invariants = []
        self.entities = {}

        self.filenames = set()

        for proof_data in json_data["proofs"]:
            IvyProof(self, proof_data)

        for proof_data in json_data["invariants"]:
            IvyInvariant(self, proof_data)

        # TODO add all referenced sequences and properties so we can list them and store associated
        # data

        self.status_out_edges = defaultdict(StableSet)
        self.status_in_edges = defaultdict(StableSet)
        for entity in self:
            for edge in entity.edges():
                self.status_out_edges[edge.source].add(edge)
                self.status_in_edges[edge.target].add(edge)

                self.status_in_edges[edge.source]  # create set if it doesn't exist

        self.status_non_entity_sources = StableSet()
        for key, in_edges in self.status_in_edges.items():
            if key.type != "entity" and not in_edges:
                self.status_non_entity_sources.add(key)

    def uniquify(self, name: str) -> str:
        if name not in self.filenames:
            self.filenames.add(name)
            return name
        i = 1
        while (candidate := f"{name}_{i}") in self.filenames:
            i += 1
        self.filenames.add(candidate)
        return candidate

    def __getitem__(self, name: IvyName) -> IvyEntity:
        return self.entities[name]

    def __iter__(self) -> Iterator[IvyEntity]:
        yield from self.entities.values()

    def proof(self, name: IvyName) -> IvyProof:
        try:
            proof = self[name]
        except KeyError:
            tl.log_error(f"could not find proof {name}")

        if not isinstance(proof, IvyProof):
            tl.log_error(f"expected {name} to be a proof but found {proof.type} ({proof.src_loc})")
        return proof

    def invariant(self, name: IvyName) -> IvyInvariant:
        try:
            invariant = self[name]
        except KeyError:
            tl.log_error(f"could not find invariant {name}")
        if not isinstance(invariant, IvyInvariant):
            tl.log_error(
                f"expected {name} to be an invariant but found "
                f"{invariant.type} ({invariant.src_loc})"
            )
        return invariant

    def status_map(self) -> IvyStatusMap:
        return IvyStatusMap(self)


class IvyStatusMap:
    data: IvyData
    current_status: dict[StatusKey, Status]

    dirty: StableSet[StatusKey]
    dirty_queue: deque[StatusKey]

    cross_dirty: StableSet[IvyName]

    def __init__(self, data: IvyData):
        self.data = data
        self.current_status = dict()

        self.dirty_queue = deque()
        self.dirty = StableSet()

        self.cross_dirty = StableSet()

        for key in data.status_non_entity_sources:
            self.set_status(key, "pass")

    def status(self, key: StatusKey) -> Status:
        return self.current_status.get(
            key,
            "pass" if key.type == "cross" else "unreachable",
        )

    def set_status(self, key: StatusKey, status: Status):
        if self.status(key) == status:
            return

        if key.type == "entity" and key.name not in self.cross_dirty:
            self.cross_dirty.add(key.name)

        self.current_status[key] = status
        for out_edge in self.data.status_out_edges[key]:
            if out_edge.target in self.dirty:
                continue
            self.dirty.add(out_edge.target)
            self.dirty_queue.append(out_edge.target)

    def iterate(self):
        import time

        start = time.time()
        while self.dirty or self.cross_dirty:
            steps = 0
            while self.dirty_queue:
                steps += 1
                key = self.dirty_queue.popleft()
                assert key not in ("cross", "step")
                self.dirty.remove(key)

                combine_status = status_or if key.type == "entity" else status_and
                status = combine_status(
                    *(self.status(edge.source) for edge in self.data.status_in_edges[key])
                )
                self.set_status(key, status)
            tl.log_warning(f"iteration took {steps} steps")

            for name in self.cross_dirty:
                self.set_status(StatusKey("cross", name), self.status(StatusKey("entity", name)))
            self.cross_dirty.clear()
        tl.log_warning("took", time.time() - start, "seconds")
