from codeanalyzer.schema import ids

def test_application_and_module_ids_put_the_app_outermost():
    assert ids.application_id("myapp") == "can://myapp"
    assert ids.module_id("myapp", "pkg/mod.py") == "can://myapp/python/pkg/mod.py"

def test_callable_signature_segment_uses_param_names():
    assert ids.callable_sig_segment("hash", ["self", "s"]) == "hash(self,s)"
    assert ids.callable_sig_segment("noargs", []) == "noargs()"

def test_child_and_ordinal_ids_compose():
    mod = ids.module_id("myapp", "pkg/mod.py")
    cls = ids.child_id(mod, "Hasher")
    fn = ids.child_id(cls, ids.callable_sig_segment("hash", ["self", "s"]))
    assert fn == "can://myapp/python/pkg/mod.py/Hasher/hash(self,s)"
    assert ids.ordinal_id(fn, "15:4") == fn + "@15:4"
    assert ids.ordinal_id(fn, "entry") == fn + "@entry"

def test_external_id_is_app_scoped_and_language_neutral():
    app = ids.application_id("myapp")
    assert ids.external_id(app, "os", "getcwd") == "can://myapp/@external/os/getcwd"
    assert ids.external_id(app, None, "print") == "can://myapp/@external/print"

def test_artifact_id_nests_under_the_app_instead_of_a_parallel_scheme():
    # Was can://artifact/<app>/<path> — a second outermost shape. Now one rule.
    assert ids.artifact_id("myapp", "pyproject.toml") == "can://myapp/artifact/pyproject.toml"

def test_every_id_shares_the_application_prefix():
    # This is what makes the prefix-scoped delete correct, so assert it directly.
    app = ids.application_id("myapp")
    for i in (
        ids.module_id("myapp", "pkg/mod.py"),
        ids.external_id(app, "os", "getcwd"),
        ids.artifact_id("myapp", "pyproject.toml"),
        ids.config_key_id(ids.artifact_id("myapp", "pyproject.toml"), "project.name"),
    ):
        assert i.startswith(app + "/"), f"{i} must sit under {app}"

def test_an_app_named_python_is_not_a_language_prefix():
    # The collision the old grammar could not produce: nothing may read the
    # language off the first segment any more.
    assert ids.module_id("python", "m.py") == "can://python/python/m.py"
