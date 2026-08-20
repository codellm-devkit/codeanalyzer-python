import pytest

from codeanalyzer.entrypoints.rules import RulesError, load_rules


def test_shipped_rules_load_and_include_flask():
    rs = load_rules()
    assert "flask" in rs.frameworks
    flask = rs.frameworks["flask"]
    assert "flask" in flask.detect
    assert any(r.id == "flask.route" for r in flask.decorators)


def test_every_shipped_rule_has_a_stable_id_and_valid_confidence():
    rs = load_rules()
    for fw in rs.frameworks.values():
        for rule in list(fw.decorators) + list(fw.bases):
            assert rule.id, "every rule needs a stable id so users can disable it"
            assert rule.confidence in {"declared", "certain", "heuristic"}


def test_malformed_user_file_raises_before_analysis(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("frameworks: [this is a list not a mapping]\n")
    with pytest.raises(RulesError):
        load_rules([bad])


def test_bare_string_disable_raises_instead_of_silently_matching_chars(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("disable: flask.route\n")
    with pytest.raises(RulesError):
        load_rules([bad])


def test_well_formed_disable_list_removes_the_shipped_rule(tmp_path):
    user = tmp_path / "user.yml"
    user.write_text("disable: [flask.route]\n")
    rs = load_rules([user])
    flask = rs.frameworks["flask"]
    assert all(r.id != "flask.route" for r in flask.decorators)


def test_user_rules_merge_additively_with_shipped(tmp_path):
    extra = tmp_path / "mine.yml"
    extra.write_text(
        "version: 1\n"
        "frameworks:\n"
        "  inhouse:\n"
        "    detect: [inhouse]\n"
        "    decorators:\n"
        "      - id: inhouse.handler\n"
        "        match: 'inhouse.app.handler'\n"
    )
    rs = load_rules([extra])
    assert "flask" in rs.frameworks          # shipped survives
    assert "inhouse" in rs.frameworks        # user added
    assert rs.rulesets == ["shipped", f"user:{extra}"]


def test_user_file_can_disable_a_shipped_rule(tmp_path):
    off = tmp_path / "off.yml"
    off.write_text("version: 1\ndisable: [flask.route]\n")
    rs = load_rules([off])
    assert not any(r.id == "flask.route" for r in rs.frameworks["flask"].decorators)
    assert any(r.id == "flask.bp-verb" for r in rs.frameworks["flask"].decorators)


def test_bad_confidence_value_is_rejected(tmp_path):
    bad = tmp_path / "c.yml"
    bad.write_text(
        "version: 1\nframeworks:\n  x:\n    decorators:\n"
        "      - id: x.y\n        match: 'x.y'\n        confidence: probably\n"
    )
    with pytest.raises(RulesError):
        load_rules([bad])
