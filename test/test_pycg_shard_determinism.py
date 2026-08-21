"""Shard outcomes are decided by the input, never by the clock (#145).

A shard that exceeded a wall-clock timeout used to be a "runaway" contributing
ZERO edges. Which shards ran slow depended on machine load, so identical runs
produced different call graphs — measured across one 2,364-file fixture:
48,595 / 43,431 / 40,224 edges for the same input and flags.

PyCG's fixpoint is `while iter < max_iter and not has_converged()`, so a shard
terminates on its own. Asking `has_converged()` afterwards distinguishes
"reached fixpoint" from "hit the cap" — a function of the input alone.
"""
import inspect

from codeanalyzer.semantic_analysis.pycg import pycg_analysis as pa


def _code_only(fn) -> str:
    """Source with comment lines and the docstring stripped.

    The explanatory comments mention `deadline`; the assertions below are about
    executable code, so compare against code alone.
    """
    src = inspect.getsource(fn)
    body = src.split('"""')
    src = body[0] + "".join(body[2:]) if len(body) > 2 else src
    return "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )


def test_no_wall_clock_deadline_governs_shard_collection():
    """Neither Ray collector may cancel work for being slow."""
    for fn in (pa.PyCG._run_fileset_shards_ray, pa.PyCG._build_sharded_ray):
        src = _code_only(fn)
        assert "ray.cancel" not in src, f"{fn.__name__}: a slow shard must not be cancelled"
        assert "deadline" not in src, f"{fn.__name__}: no wall-clock deadline"


def test_module_has_no_wall_clock_bound_left():
    """Whole-module sweep: every path, not just the ones named above.

    The timeout was reachable from four collection paths; a check scoped to
    one function would pass while another still dropped shards by the clock.
    """
    src = "\n".join(
        ln for ln in inspect.getsource(pa).splitlines()
        if not ln.strip().startswith("#")
    )
    for banned in ("SIGALRM", "signal.alarm", "ray.cancel", "shard_timeout"):
        assert banned not in src, f"{banned} still reachable in pycg_analysis"


def test_sequential_runner_does_not_bound_shards_by_time():
    src = _code_only(pa.PyCG._run_fileset_shards_seq)
    assert "_shard_timeout" not in src, "shard runs must not be wall-clock bounded"
    assert "converged" in src, "runaway classification must use PyCG convergence"


def test_runaways_carry_their_partial_edges():
    """A capped fixpoint is a sound under-approximation — keep it, don't drop it."""
    src = inspect.getsource(pa.PyCG._run_fileset_shards_seq)
    assert "runaways.append((files, edges))" in src

    loop = inspect.getsource(pa.PyCG._build_sharded_planned)
    assert "all_edges.extend(partial)" in loop, (
        "an irreducible shard must contribute its capped-fixpoint edges, not zero"
    )


def test_decomposition_can_reach_a_single_file():
    """A floor of 10 files left small runaways unsplittable, forcing the drop path."""
    assert pa.PyCG._PYCG_DECOMP_FLOOR == 1


def test_worker_reports_convergence():
    src = inspect.getsource(pa._pycg_shard_worker)
    assert "_analyze_with_convergence" in src
    assert "return [(src, dst, count) for (src, dst), count in edge_counts.items()], converged" in src


class _FakeCG:
    """Stands in for PyCG's CallGraphGenerator, with its exact loop shape.

    Verbatim from PyCG 0.0.7 `CallGraphGenerator.analyze`:
        while (self.max_iter < 0 or iter_cnt < self.max_iter) and (
            not self.has_converged()
        ): ...
    followed by a CallGraphProcessor pass that runs past the loop.
    """

    def __init__(self, converge_after, max_iter):
        self.converge_after = converge_after
        self.max_iter = max_iter
        self.passes = 0
        self.post_loop_pass_ran = False

    def has_converged(self):
        return self.passes >= self.converge_after

    def analyze(self):
        iter_cnt = 0
        while (self.max_iter < 0 or iter_cnt < self.max_iter) and (
            not self.has_converged()
        ):
            self.passes += 1
            iter_cnt += 1
        self.post_loop_pass_ran = True  # CallGraphProcessor


def test_convergence_true_when_fixpoint_reached():
    cg = _FakeCG(converge_after=3, max_iter=50)
    assert pa._analyze_with_convergence(cg) is True
    assert cg.passes == 3
    assert cg.post_loop_pass_ran


def test_convergence_false_when_capped_at_max_iter():
    cg = _FakeCG(converge_after=99, max_iter=5)
    assert pa._analyze_with_convergence(cg) is False
    assert cg.passes == 5


def test_boundary_converges_exactly_at_the_cap():
    """Needing exactly max_iter passes is convergence, not a runaway."""
    cg = _FakeCG(converge_after=5, max_iter=5)
    assert pa._analyze_with_convergence(cg) is True


def test_convergence_read_is_not_a_post_hoc_call():
    """The post-loop pass must not be able to flip the verdict.

    Reading `has_converged()` after `analyze()` returns would consult state the
    CallGraphProcessor pass has already moved. This fake reports divergence
    once that pass has run; the verdict must still be True.
    """

    class _MovesStateAfterLoop(_FakeCG):
        def has_converged(self):
            if self.post_loop_pass_ran:
                return False
            return super().has_converged()

    cg = _MovesStateAfterLoop(converge_after=2, max_iter=50)
    assert pa._analyze_with_convergence(cg) is True
    assert cg.has_converged() is False  # a post-hoc call would have said runaway


def test_original_method_is_restored():
    cg = _FakeCG(converge_after=1, max_iter=50)
    before = _FakeCG.has_converged
    pa._analyze_with_convergence(cg)
    assert "has_converged" not in vars(cg), "instance shim must be removed"
    assert cg.has_converged.__func__ is before


def test_restored_even_when_analyze_raises():
    cg = _FakeCG(converge_after=1, max_iter=50)
    cg.analyze = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        pa._analyze_with_convergence(cg)
    except RuntimeError:
        pass
    assert "has_converged" not in vars(cg)


def test_raised_cap_does_not_buy_an_extra_pass():
    """The +1 exists to ask one more question, not to run one more pass."""
    cg = _FakeCG(converge_after=99, max_iter=5)
    assert pa._analyze_with_convergence(cg) is False
    assert cg.passes == 5, "a diverging shard must still stop at max_iter"
    assert cg.max_iter == 5, "the cap must be restored"


def test_unbounded_max_iter_is_left_alone():
    cg = _FakeCG(converge_after=4, max_iter=-1)
    assert pa._analyze_with_convergence(cg) is True
    assert cg.passes == 4
    assert cg.max_iter == -1


def test_zero_max_iter_runs_nothing():
    cg = _FakeCG(converge_after=99, max_iter=0)
    assert pa._analyze_with_convergence(cg) is True, "nothing ran; nothing to re-split"
    assert cg.passes == 0


def test_irreducible_runaway_keeps_its_partial_edges(tmp_path):
    """A shard that cannot be split contributes its edges, not zero.

    Previously such a shard fell back to "Jedi-only coverage" — the whole
    reason a load-dependent timeout cost 5,164 edges between two identical
    runs. A capped fixpoint is a sound under-approximation, so the edges it
    did derive are real and belong in the output.
    """
    from codeanalyzer.schema.py_schema import PyCallEdge, PyCallable, PyModule
    from codeanalyzer.semantic_analysis.pycg.pycg_analysis import (
        PyCG,
        _PyCGCallableResolver,
    )

    st, jedi = {}, []
    for i in range(6):
        path = f"/proj/m{i}.py"
        st[path] = PyModule(
            file_path=path,
            module_name=f"m{i}",
            functions={"f": PyCallable(signature=f"m{i}.f", name="f", path=path)},
        )
        if i:
            jedi.append(
                PyCallEdge(src=f"m{i-1}.f", dst=f"m{i}.f", weight=1, prov=["jedi"])
            )

    pycg = PyCG(tmp_path, shard_ceiling=6)

    def never_converges(shards):
        """Every shard diverges, at every size — nothing is ever reducible."""
        runaways = [
            (
                files,
                [
                    PyCallEdge(src=f, dst="partial", weight=1, prov=["pycg"])
                    for f in files
                ],
            )
            for files in shards
        ]
        return [], runaways

    pycg._run_fileset_shards_seq = never_converges
    edges = pycg._build_sharded_planned(jedi, st, _PyCGCallableResolver(set()))

    srcs = {e.src for e in edges}
    assert srcs == {f"/proj/m{i}.py" for i in range(6)}, (
        "every file's capped-fixpoint edges must survive decomposition bottoming out"
    )
