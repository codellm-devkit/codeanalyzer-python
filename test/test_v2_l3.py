from codeanalyzer.dataflow.identity import IdentityMap

class _Node:
    def __init__(self, id, start_line, start_column, kind):
        self.id, self.start_line, self.start_column, self.kind = id, start_line, start_column, kind

class _CFG:
    def __init__(self, nodes, entry_id, exit_id):
        self._n = {n.id: n for n in nodes}; self.nodes = nodes
        self.entry_id, self.exit_id = entry_id, exit_id
    def node_by_id(self, i): return self._n[i]

class _PDG:
    def __init__(self, cfg): self.cfg = cfg

def test_ordinal_ids_for_entry_exit_and_statements():
    nodes = [_Node(0, 1, 0, "entry"), _Node(1, 2, 4, "statement"), _Node(2, 3, 4, "exit")]
    pdg = _PDG(_CFG(nodes, entry_id=0, exit_id=2))
    im = IdentityMap.for_function("can://python/app/m.py/f()", pdg)
    assert im.ordinal(0) == "can://python/app/m.py/f()@entry"
    assert im.ordinal(1) == "can://python/app/m.py/f()@2:4"
    assert im.ordinal(2) == "can://python/app/m.py/f()@exit"
    assert set(im.node_ids()) == {0, 1, 2}
