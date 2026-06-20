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
    "CREATE CONSTRAINT py_symbol_sig IF NOT EXISTS FOR (s:PySymbol) REQUIRE s.signature IS UNIQUE",
    "CREATE CONSTRAINT py_app_name IF NOT EXISTS FOR (a:PyApplication) REQUIRE a.name IS UNIQUE",
    "CREATE CONSTRAINT py_module_key IF NOT EXISTS FOR (m:PyModule) REQUIRE m.file_key IS UNIQUE",
    "CREATE CONSTRAINT py_package_name IF NOT EXISTS FOR (p:PyPackage) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT py_decorator_name IF NOT EXISTS FOR (d:PyDecorator) REQUIRE d.name IS UNIQUE",
    "CREATE CONSTRAINT py_callsite_id IF NOT EXISTS FOR (c:PyCallSite) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT py_attribute_id IF NOT EXISTS FOR (a:PyAttribute) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT py_variable_id IF NOT EXISTS FOR (v:PyVariable) REQUIRE v.id IS UNIQUE",
]

INDEXES: List[str] = [
    "CREATE INDEX py_callable_name IF NOT EXISTS FOR (c:PyCallable) ON (c.name)",
    "CREATE INDEX py_class_name IF NOT EXISTS FOR (c:PyClass) ON (c.name)",
    "CREATE FULLTEXT INDEX py_code_fts IF NOT EXISTS FOR (c:PyCallable) ON EACH [c.code, c.docstring]",
]
