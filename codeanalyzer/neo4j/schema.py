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

"""The Cypher DDL — uniqueness constraints and indexes — shared by both writers.
Run BEFORE any load so MERGE uses an index seek (not a label scan) and the
identity invariant is enforced by the database. Every statement is idempotent
(``IF NOT EXISTS``).
"""
from typing import List

CONSTRAINTS: List[str] = [
    "CREATE CONSTRAINT symbol_sig IF NOT EXISTS FOR (s:Symbol) REQUIRE s.signature IS UNIQUE",
    "CREATE CONSTRAINT app_name IF NOT EXISTS FOR (a:Application) REQUIRE a.name IS UNIQUE",
    "CREATE CONSTRAINT module_key IF NOT EXISTS FOR (m:Module) REQUIRE m.file_key IS UNIQUE",
    "CREATE CONSTRAINT package_name IF NOT EXISTS FOR (p:Package) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT decorator_name IF NOT EXISTS FOR (d:Decorator) REQUIRE d.name IS UNIQUE",
    "CREATE CONSTRAINT callsite_id IF NOT EXISTS FOR (c:CallSite) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT attribute_id IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT variable_id IF NOT EXISTS FOR (v:Variable) REQUIRE v.id IS UNIQUE",
]

INDEXES: List[str] = [
    "CREATE INDEX callable_name IF NOT EXISTS FOR (c:Callable) ON (c.name)",
    "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.name)",
    "CREATE FULLTEXT INDEX code_fts IF NOT EXISTS FOR (c:Callable) ON EACH [c.code, c.docstring]",
]
