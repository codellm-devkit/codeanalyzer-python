from pathlib import Path

from codeanalyzer.schema.py_schema import byte_offsets
from codeanalyzer.syntactic_analysis.symbol_table_builder import SymbolTableBuilder


def test_byte_offsets_slice_source_exactly():
    source = "def f():\n    return 1\n"
    # `return 1` is line 2, cols 4..12 (0-based, end exclusive per ast end_col_offset)
    lo, hi = byte_offsets(source, 2, 4, 2, 12)
    assert source.encode("utf-8")[lo:hi].decode("utf-8") == "return 1"


def test_byte_offsets_multibyte_safe():
    source = "x = 'é'\ny = 2\n"  # 'é' is 2 bytes in utf-8
    lo, hi = byte_offsets(source, 2, 0, 2, 5)
    assert source.encode("utf-8")[lo:hi].decode("utf-8") == "y = 2"


def test_module_stores_source_and_callable_span_slices_it(tmp_path: Path):
    f = tmp_path / "m.py"
    f.write_text("def f(a):\n    return a\n", encoding="utf-8")
    mod = SymbolTableBuilder(tmp_path, None).build_pymodule_from_file(f)
    assert mod.source == "def f(a):\n    return a\n"
    fn = next(iter(mod.functions.values()))
    lo, hi = fn.span.bytes
    assert mod.source.encode("utf-8")[lo:hi].decode("utf-8").startswith("def f(a):")
