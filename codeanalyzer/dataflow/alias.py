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

"""Stage 5a of the level-3 dataflow ladder: the may-alias oracle.

Python has no in-process Andersen-style points-to library, so the locked
substrate decision (#67) is the **type-based MVP stub**: two access paths may
alias iff they share a non-empty field suffix and their bases' inferred types
are compatible — where an unknown type is compatible with everything
(sound-leaning by contract). Bare locals never alias each other (Python has
no pointers to locals; closure and global sharing ride the capture/global
mechanisms instead).

The oracle is frozen: downstream stages call :meth:`may_alias` and never
reach into its internals, so upgrading to a real points-to substrate later is
a drop-in replacement.

Type information comes from the symbol table Jedi already populated
(``PyVariableDeclaration.type`` / ``PyCallableParameter.type``); the oracle
works with whatever subset is present.
"""

from __future__ import annotations

from typing import Dict, Optional

from codeanalyzer.dataflow.access_paths import base_of, suffix_of


def _normalize(type_name: Optional[str]) -> Optional[str]:
    if not type_name:
        return None
    t = type_name.strip()
    # `Optional[X]`, `X | None`, quotes, module prefixes: compare last simple name.
    for wrapper in ("Optional[", "typing.Optional["):
        if t.startswith(wrapper) and t.endswith("]"):
            t = t[len(wrapper):-1]
    t = t.split("|")[0].strip()
    t = t.split("[")[0].strip()
    return t.split(".")[-1] or None


class TypeBasedAliasOracle:
    """``may_alias(p1, p2)`` for access paths in one function scope.

    ``base_types`` maps base names to their inferred type names (absent or
    ``None`` = unknown = may alias anything with the same suffix).
    """

    def __init__(self, base_types: Optional[Dict[str, Optional[str]]] = None):
        self._types = {k: _normalize(v) for k, v in (base_types or {}).items()}

    def may_alias(self, path_a: str, path_b: str) -> bool:
        if path_a == path_b:
            return True
        suffix_a, suffix_b = suffix_of(path_a), suffix_of(path_b)
        if not suffix_a and not suffix_b:
            # Two distinct bare bases never alias (locals are not
            # addressable); base sharing rides assignments in the DDG.
            return False
        # Field-sensitive up to prefix compatibility: identical suffixes may
        # denote one location; a bare base (whole-object read/write) observes
        # every field of its object, so an empty suffix is prefix-compatible
        # with any; wildcards from k-truncation match anything deeper.
        sa = suffix_a.rstrip("*").rstrip(".")
        sb = suffix_b.rstrip("*").rstrip(".")
        prefix_compatible = (
            sa == sb
            or sa.startswith(sb)
            or sb.startswith(sa)
            or suffix_a.endswith("*")
            or suffix_b.endswith("*")
        )
        if not prefix_compatible:
            return False
        type_a = self._types.get(base_of(path_a))
        type_b = self._types.get(base_of(path_b))
        if type_a is None or type_b is None:
            return True  # unknown: conservatively compatible
        return type_a == type_b
