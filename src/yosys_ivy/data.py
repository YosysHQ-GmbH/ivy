from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Collection, Iterator

from yosys_mau.stable_set import StableSet

from .sccs import find_sccs


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
