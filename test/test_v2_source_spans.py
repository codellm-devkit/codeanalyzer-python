from codeanalyzer.schema.py_schema import byte_offsets


def test_byte_offsets_slice_source_exactly():
    source = "def f():\n    return 1\n"
    # `return 1` is line 2, cols 4..12 (0-based, end exclusive per ast end_col_offset)
    lo, hi = byte_offsets(source, 2, 4, 2, 12)
    assert source.encode("utf-8")[lo:hi].decode("utf-8") == "return 1"


def test_byte_offsets_multibyte_safe():
    source = "x = 'é'\ny = 2\n"  # 'é' is 2 bytes in utf-8
    lo, hi = byte_offsets(source, 2, 0, 2, 5)
    assert source.encode("utf-8")[lo:hi].decode("utf-8") == "y = 2"
