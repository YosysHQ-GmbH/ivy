from __future__ import annotations

from typing import Collection, Iterator, TypeVar

from yosys_mau.stable_set import StableSet

T = TypeVar("T")


def find_sccs(nodes: dict[T, Collection[T]]) -> list[StableSet[T]]:
    """Iterative implementation of Tarjan's algorithm for finding strongly connected
    components (in topological order)."""
    components: list[StableSet[T]] = []
    low: dict[T, int] = {}
    dfs: list[tuple[T, Iterator[T] | None, int]] = []
    stack: list[T] = []
    for start in nodes:
        if start in low:
            continue

        dfs.append((start, None, len(low)))

        while dfs:
            node, node_edges, num = dfs.pop()
            if node_edges is None:
                if node in low:
                    continue
                else:
                    low[node] = num
                    stack.append(node)
                node_edges = iter(nodes.get(node, ()))

            try:
                next_edge = next(node_edges)
            except StopIteration:
                pass
            else:
                dfs.append((node, node_edges, num))
                dfs.append((next_edge, None, len(low)))
                continue

            val = low[node]
            for edge in nodes.get(node, ()):
                if edge in low:
                    val = min(val, low[edge])

            low[node] = val

            if num == val:
                component: StableSet[T] = StableSet()
                while stack:
                    component_node = stack.pop()
                    component.add(component_node)
                    low[component_node] = len(nodes)
                    if component_node == node:
                        break

                components.append(component)
    return components
