from yosys_ivy.sccs import find_sccs


def test_find_sccs():
    assert find_sccs({1: [2], 2: [3], 3: [1]}) == [{1, 2, 3}]
    assert find_sccs({1: [2], 2: [3], 3: [2]}) == [{2, 3}, {1}]
    assert find_sccs({1: [2], 2: [3], 3: []}) == [{3}, {2}, {1}]
