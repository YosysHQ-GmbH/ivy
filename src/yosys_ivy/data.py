from __future__ import annotations

import abc
import heapq
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, NamedTuple

import yosys_mau.task_loop as tl
from yosys_mau.stable_set import StableSet

from yosys_ivy.sccs import find_sccs

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

    @property
    def rtlil(self) -> str:
        """RTLIL name after uniquification"""
        return ".".join([self.parts[0], *self.parts[1:-1:2]]) + f"/{self.parts[-1]}"

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

        for assert_json in self.json_data.get("assert", []):
            IvyAssert(self, assert_json)

        for export_json in self.json_data.get("export", []):
            IvyExport(self, export_json)

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

    status_graph: IvyStatusGraph = field(repr=False)

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

        self.status_graph = IvyStatusGraph(self)


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


class IvyStatusGraph:
    # We map StatusKey to ints that are topologically ordered (across SCCs) as that makes the inner
    # loop quite a bit faster (due to improved visting order and cheaper operations).

    out_edges: dict[StatusKey, StableSet[StatusEdge]]
    in_edges: dict[StatusKey, StableSet[StatusEdge]]

    out_edges_list: list[list[int]]
    in_edges_list: list[list[int]]

    order: dict[StatusKey, int]
    order_list: list[StatusKey]

    cross_order_map: list[int | None]
    cross_indices: list[int]

    non_entity_sources: StableSet[StatusKey]

    def __init__(self, data: IvyData):

        self.out_edges = defaultdict(StableSet)
        self.in_edges = defaultdict(StableSet)
        for entity in data:
            for edge in entity.edges():
                self.out_edges[edge.source].add(edge)
                self.in_edges[edge.target].add(edge)

                self.in_edges[edge.source]  # create set if it doesn't exist
                self.out_edges[edge.target]  # create set if it doesn't exist

        self.non_entity_sources = StableSet()
        for key, in_edges in self.in_edges.items():
            if key.type not in ("entity", "step") and not in_edges:
                self.non_entity_sources.add(key)

        self.order = {}
        self.order_list = []
        for scc in find_sccs(
            {key: [edge.source for edge in edges] for key, edges in self.in_edges.items()}
        ):
            if len(scc) > 1:
                tl.log_debug(f"found scc {len(scc)}")
            for key in scc:
                self.order[key] = len(self.order)
                self.order_list.append(key)

        self.cross_order_map = [
            self.order.get(StatusKey("cross", key.name)) if key.type == "entity" else None
            for key in self.order_list
        ]

        self.cross_indices = [
            i for i, key in enumerate(self.order_list) if key.type == "cross"
        ]

        self.out_edges_list = [
            sorted([self.order[edge.target] for edge in self.out_edges[source]])
            for source in self.order_list
        ]

        self.in_edges_list = [
            sorted([self.order[edge.source] for edge in self.in_edges[target]])
            for target in self.order_list
        ]


class IvyStatusMap:
    status_graph: IvyStatusGraph

    current_status: list[Status]

    dirty: list[bool]
    dirty_queue: list[int]

    cross_dirty: list[int]
    cross_dirty_list: list[int]

    def __init__(self, data: IvyData):
        self.status_graph = data.status_graph

        self.current_status = ["unreachable"] * len(self.status_graph.order)

        self.dirty_queue = []
        self.dirty = [False] * len(self.current_status)

        self.cross_dirty = [False] * len(self.current_status)
        self.cross_dirty_map = [None] * len(self.current_status)

        self.cross_dirty_list = []

        for key in self.status_graph.non_entity_sources:
            self.set_status(key, "pass")

    def status(self, key: StatusKey) -> Status:
        return self._status(self.status_graph.order[key])

    def _status(self, index: int) -> Status:
        return self.current_status[index]

    def _push_dirty(self, index: int):
        self.dirty[index] = True
        heapq.heappush(self.dirty_queue, index)

    def _pop_dirty(self) -> int:
        return heapq.heappop(self.dirty_queue)

    def set_status(self, key: StatusKey, status: Status):
        index = self.status_graph.order[key]
        self._set_status(index, status)

    def _set_status(self, index: int, status: Status):
        if self._status(index) == status:
            return

        cross = self.status_graph.cross_order_map[index]
        if cross is not None and not self.cross_dirty[index]:
            self.cross_dirty[index] = True
            self.cross_dirty_list.append(index)

        self.current_status[index] = status
        for out_edge in self.status_graph.out_edges_list[index]:
            if not self.dirty[out_edge]:
                self._push_dirty(out_edge)

    def iterate(self):
        import time

        start = time.time()
        while self.dirty_queue or self.cross_dirty_list:
            steps = 0
            while self.dirty_queue:
                steps += 1
                index = self._pop_dirty()
                key = self.status_graph.order_list[index]
                self.dirty[index] = False

                combine_status = status_or if key.type == "entity" else status_and
                status = combine_status(
                    *(self._status(edge) for edge in self.status_graph.in_edges_list[index])
                )
                self._set_status(index, status)
            tl.log_debug(f"iteration took {steps} steps")

            for index in self.cross_dirty_list:
                self.cross_dirty[index] = False
                cross = self.status_graph.cross_order_map[index]
                assert cross is not None
                self._set_status(cross, self._status(index))
            self.cross_dirty_list = []
        tl.log_debug("took", time.time() - start, "seconds")
