################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Stage 4 of the level-3/4 dataflow ladder: the primary L4 may-alias oracle.

``python-scalpel`` (SMAT-Lab/Scalpel) is the substrate decided by the Stage-0
spike (issue #70): not a turnkey points-to engine but an **SSA + copy/const**
producer. :class:`ScalpelAliasOracle` consumes its *solved* state — it never
forks or re-runs Scalpel's solver — and turns the copy/const records into
per-function copy-closure equivalence classes:

    ``from scalpel.SSA.const import SSA``
    ``ssa_results, const_dict = SSA().compute_SSA(func_cfg)``

``const_dict`` maps ``(name, version)`` to the ``ast`` value node that defined
that SSA name.  A value that is an ``ast.Name`` is a whole-object **copy edge**
(``b = a`` ⇒ ``('b', 0) -> Name 'a'``); a value that is an ``ast.Attribute`` is
an attribute-path copy (``q = p.x`` ⇒ ``('q', 0) -> Attribute p.x``).  The
transitive closure of these edges is a union-find over access-path strings.

``may_alias(path_a, path_b)`` is TRUE iff the paths are identical, or their
bases share a copy-closure class (or the whole paths do) *and* their field
suffixes are prefix-compatible (the same suffix logic the frozen
:class:`~codeanalyzer.dataflow.alias.TypeBasedAliasOracle` uses).  Anything the
copy closure cannot resolve — unrelated bases, constructs Scalpel does not
model (heap points-to, two distinct params, container elements) — is delegated
to a wrapped ``TypeBasedAliasOracle``, which supplies the type-guided verdict
(incompatible concrete types ⇒ not aliased; unknown type ⇒ may-alias).

The oracle is **sound-leaning**: adding copy edges only ever yields *more*
may-alias answers, and every uncertainty widens to the type-based fallback
rather than silently returning ``False``.  All Scalpel use is guarded — a build
or query failure degrades to the wrapped fallback, never an exception.

The public interface is frozen and identical to ``TypeBasedAliasOracle``:
``may_alias(path_a: str, path_b: str) -> bool``.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, Optional

from codeanalyzer.dataflow.access_paths import base_of, suffix_of
from codeanalyzer.dataflow.alias import TypeBasedAliasOracle
from codeanalyzer.utils import logger

# All subscripts collapse to ``[*]`` to match the access-path grammar
# (``base(.field | [*])*``) the rest of the dataflow ladder speaks.
_SUBSCRIPT = re.compile(r"\[[^\[\]]*\]")

# Log the "Scalpel unavailable → fallback" notice at most once per process so a
# large project does not spam one line per function.
_fallback_logged = False


def _normalize_path(path: str) -> str:
    return _SUBSCRIPT.sub("[*]", path)


def _suffix_prefix_compatible(path_a: str, path_b: str) -> bool:
    """Field-suffix compatibility, identical to the frozen
    ``TypeBasedAliasOracle`` rule: identical suffixes may denote one location; a
    bare base (whole-object access) observes every field, so an empty suffix is
    compatible with any; k-truncation wildcards (``*``) match anything deeper."""
    suffix_a, suffix_b = suffix_of(path_a), suffix_of(path_b)
    sa = suffix_a.rstrip("*").rstrip(".")
    sb = suffix_b.rstrip("*").rstrip(".")
    return bool(
        sa == sb
        or sa.startswith(sb)
        or sb.startswith(sa)
        or suffix_a.endswith("*")
        or suffix_b.endswith("*")
    )


def _note_fallback(reason: str) -> None:
    global _fallback_logged
    if not _fallback_logged:
        logger.info(
            "Scalpel may-alias oracle unavailable (%s); using TypeBasedAliasOracle fallback.",
            reason,
        )
        _fallback_logged = True


class ScalpelAliasOracle:
    """L4 may-alias oracle backed by Scalpel's SSA copy/const facts.

    Construct from a raw ``const_dict`` (``(name, version) -> ast value``) or,
    more commonly, via :meth:`from_function`, which imports Scalpel and computes
    the SSA state for a function AST.  ``base_types`` (base name → inferred type)
    feeds the wrapped :class:`TypeBasedAliasOracle` used for everything the copy
    closure cannot decide.
    """

    def __init__(
        self,
        const_dict: Optional[dict] = None,
        base_types: Optional[Dict[str, Optional[str]]] = None,
        fallback: Optional[TypeBasedAliasOracle] = None,
    ):
        self._fallback = fallback or TypeBasedAliasOracle(base_types)
        self._parent: Dict[str, str] = {}
        self._seen: set[str] = set()
        try:
            self._build_classes(const_dict or {})
        except Exception:  # pragma: no cover — never let a build quirk escape
            logger.debug(
                "scalpel copy-closure build failed; oracle will lean on fallback",
                exc_info=True,
            )

    # -- construction --------------------------------------------------------

    @classmethod
    def from_function(
        cls,
        func_ast: ast.AST,
        base_types: Optional[Dict[str, Optional[str]]] = None,
        fallback: Optional[TypeBasedAliasOracle] = None,
        name: Optional[str] = None,
    ) -> "ScalpelAliasOracle":
        """Build from a function AST by consuming Scalpel's solved SSA state.

        Imports Scalpel lazily (``ImportError`` if the optional dependency is
        absent) and reuses the *same source* both graphs are built from — the
        function's unparsed text — so the join is identity, not a fuzzy match.
        Raises on any build failure; :func:`make_alias_oracle` is the total,
        never-raising entry point callers should prefer.
        """
        from scalpel.SSA.const import SSA
        from scalpel.cfg import CFGBuilder

        src = ast.unparse(func_ast)
        fname = name or getattr(func_ast, "name", None)
        module_cfg = CFGBuilder().build_from_src(fname or "module", src)
        func_cfg = cls._select_func_cfg(module_cfg, fname)
        if func_cfg is None:
            raise ValueError("scalpel produced no function CFG for the given AST")
        # Consume the solved state; never re-run the solver ourselves.
        _ssa_results, const_dict = SSA().compute_SSA(func_cfg)
        return cls(const_dict, base_types=base_types, fallback=fallback)

    @staticmethod
    def _select_func_cfg(module_cfg, fname: Optional[str]):
        """Pick the target function's CFG out of the module CFG's
        ``functioncfgs`` (keyed ``(entry_id, func_name)``)."""
        cfgs = getattr(module_cfg, "functioncfgs", None) or {}
        if fname is not None:
            for key, fcfg in cfgs.items():
                if isinstance(key, tuple) and len(key) >= 2 and key[1] == fname:
                    return fcfg
        return next(iter(cfgs.values()), None)

    # -- copy-closure over const_dict ----------------------------------------

    def _build_classes(self, const_dict: dict) -> None:
        for key, value in const_dict.items():
            lhs = self._key_to_path(key)
            rhs = self._value_to_path(value)
            if lhs is None or rhs is None:
                continue
            self._union(lhs, rhs)

    @staticmethod
    def _key_to_path(key) -> Optional[str]:
        name = key[0] if isinstance(key, tuple) and key else key
        if not isinstance(name, str):
            return None
        return _normalize_path(name)

    @staticmethod
    def _value_to_path(value) -> Optional[str]:
        # ``ast.Name`` value ⇒ whole-object copy edge (name ↔ name).
        if isinstance(value, ast.Name):
            return _normalize_path(value.id)
        # ``ast.Attribute`` value ⇒ attribute-path copy (name ↔ base.field...).
        if isinstance(value, ast.Attribute):
            try:
                return _normalize_path(ast.unparse(value))
            except Exception:
                return None
        return None

    # -- union-find ----------------------------------------------------------

    def _find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:  # path compression
            self._parent[x], x = root, self._parent[x]
        return root

    def _union(self, a: str, b: str) -> None:
        self._seen.add(a)
        self._seen.add(b)
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[rb] = ra

    def _merged(self, a: str, b: str) -> bool:
        # Only trust a shared root when *both* tokens were actually observed in
        # the copy closure — otherwise two distinct unseen tokens are singleton
        # classes and must not be treated as related.
        return a in self._seen and b in self._seen and self._find(a) == self._find(b)

    # -- frozen interface ----------------------------------------------------

    def may_alias(self, path_a: str, path_b: str) -> bool:
        if path_a == path_b:
            return True
        try:
            na, nb = _normalize_path(path_a), _normalize_path(path_b)
            base_a, base_b = base_of(na), base_of(nb)
            if base_a == base_b:
                # Same object: purely field-sensitive (distinct fields do not
                # alias); matches the frozen TypeBasedAliasOracle decision.
                return _suffix_prefix_compatible(na, nb)
            # Distinct bases that Scalpel proved to be copies (or whole paths
            # that are copies) alias iff their suffixes are prefix-compatible.
            if self._merged(base_a, base_b) or self._merged(na, nb):
                return _suffix_prefix_compatible(na, nb)
        except Exception:
            logger.debug(
                "scalpel may_alias failed; delegating to fallback", exc_info=True
            )
        # Unresolved by the copy closure: hand off to the type-guided fallback
        # (sound-leaning — when uncertain it over-approximates to True).
        return self._fallback.may_alias(path_a, path_b)


def make_alias_oracle(pycallable, func_ast, base_types) -> object:
    """Total selector for the L4 may-alias oracle.

    Returns a :class:`ScalpelAliasOracle` when ``python-scalpel`` is importable
    *and* builds successfully on ``func_ast``; otherwise logs once (INFO) and
    returns a :class:`TypeBasedAliasOracle` over ``base_types``.  Never raises —
    mirrors how ``pipeline.passes.pycg_call_graph_edges`` degrades on a
    missing/failed PyCG.
    """
    fallback = TypeBasedAliasOracle(base_types)
    try:
        return ScalpelAliasOracle.from_function(
            func_ast, base_types=base_types, fallback=fallback
        )
    except ImportError:
        _note_fallback("python-scalpel not installed")
        return fallback
    except Exception:
        _note_fallback("scalpel alias build failed")
        logger.debug("scalpel alias oracle build error", exc_info=True)
        return fallback
