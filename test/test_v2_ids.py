from codeanalyzer.schema import ids

def test_application_and_module_ids():
    assert ids.application_id("myapp") == "can://python/myapp"
    assert ids.module_id("myapp", "pkg/mod.py") == "can://python/myapp/pkg/mod.py"

def test_callable_signature_segment_uses_param_names():
    assert ids.callable_sig_segment("hash", ["self", "s"]) == "hash(self,s)"
    assert ids.callable_sig_segment("noargs", []) == "noargs()"

def test_child_and_ordinal_ids_compose():
    mod = ids.module_id("myapp", "pkg/mod.py")
    cls = ids.child_id(mod, "Hasher")
    fn = ids.child_id(cls, ids.callable_sig_segment("hash", ["self", "s"]))
    assert fn == "can://python/myapp/pkg/mod.py/Hasher/hash(self,s)"
    assert ids.ordinal_id(fn, "15:4") == fn + "@15:4"
    assert ids.ordinal_id(fn, "entry") == fn + "@entry"
