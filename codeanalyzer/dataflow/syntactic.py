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

"""The L3 (syntactic) alias oracle: two access paths alias iff they are the
identical path. Bypasses the type-based may-alias so def-use yields only
name-equality (textual) edges — the alias-derived edges are the L4 delta."""

from __future__ import annotations


class SyntacticOracle:
    def may_alias(self, path_a: str, path_b: str) -> bool:
        return path_a == path_b
