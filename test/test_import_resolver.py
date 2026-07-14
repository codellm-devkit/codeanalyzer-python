from pathlib import Path

from codeanalyzer.schema import PyApplication
from codeanalyzer.syntactic_analysis.import_resolver import resolve_imports
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def _build_app(project_dir: Path) -> PyApplication:
    builder = SymbolTableBuilder(project_dir, None)
    table = {
        str(p): builder.build_pymodule_from_file(p)
        for p in sorted(project_dir.rglob("*.py"))
    }
    return PyApplication(symbol_table=table)


def _imports_by_spelling_and_name(module):
    return {(im.module, im.name): im for im in module.imports}


def test_absolute_and_relative_imports_resolve_to_analyzed_modules(single_functionalities__internal_imports):
    root = single_functionalities__internal_imports
    app = _build_app(root)

    resolve_imports(app, root)

    util_key = str(root / "pkg" / "util.py")
    consumer_key = str(root / "pkg" / "consumer.py")

    main = app.symbol_table[str(root / "main.py")]
    main_imports = _imports_by_spelling_and_name(main)
    assert main_imports[("os", "os")].resolved_module is None
    assert main_imports[("pkg.util", "pkg.util")].resolved_module == util_key
    assert main_imports[("pkg", "consumer")].resolved_module == consumer_key

    consumer = app.symbol_table[consumer_key]
    consumer_imports = _imports_by_spelling_and_name(consumer)
    assert consumer_imports[("pkg", "util")].resolved_module == util_key
    assert consumer_imports[(".", "u")].resolved_module == util_key
    assert consumer_imports[(".util", "helper")].resolved_module == util_key


def test_package_itself_resolves_to_its_init(single_functionalities__internal_imports):
    root = single_functionalities__internal_imports
    app = _build_app(root)

    resolve_imports(app, root)

    init_key = str(root / "pkg" / "__init__.py")
    main = app.symbol_table[str(root / "main.py")]
    pkg_util = next(im for im in main.imports if im.module == "pkg.util")
    assert pkg_util.resolved_module == str(root / "pkg" / "util.py")
    # `from pkg import consumer` resolved the submodule; a plain `import pkg`
    # would land on the package __init__ — pin that via the resolver directly:
    from codeanalyzer.syntactic_analysis.import_resolver import _dotted_candidates
    assert _dotted_candidates(app, root)["pkg"] == init_key


def test_projection_emits_module_and_package_targets(single_functionalities__internal_imports):
    from codeanalyzer.neo4j.project import project

    root = single_functionalities__internal_imports
    app = _build_app(root)
    resolve_imports(app, root)

    rows = project(app, "internal-imports")

    py_imports = [e for e in rows.edges if e.type == "PY_IMPORTS"]
    module_targets = [e for e in py_imports if e.to_ref.label == "PyModule"]
    package_targets = [e for e in py_imports if e.to_ref.label == "PyPackage"]

    assert {e.to_ref.value for e in module_targets} >= {
        str(root / "pkg" / "util.py"),
        str(root / "pkg" / "consumer.py"),
    }
    assert {e.to_ref.value for e in package_targets} == {"os"}
    assert all(e.props.get("module") for e in py_imports)
