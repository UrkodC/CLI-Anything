"""Unit tests for the guards that run before every live command (pure checks, no Premiere needed)."""
from __future__ import annotations

import json

import pytest

from cli_anything.premiere.core import guard
from cli_anything.premiere.core.bridge import RECOVERY_FILE, Heartbeat, mark_recovery
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    MARKER_SHAPES, bridge_dir, restore_marker_mode, unreadable_marker)

COPY = ("Fall Claude.prproj", "/p/Fall Claude.prproj", True)
OWN = ("Family film.prproj", "/p/Family film.prproj", True)


REAL_SERVER_VERSION = guard.server_version      # the autouse fixture below replaces guard.server_version


@pytest.fixture(autouse=True)
def _tested_server(monkeypatch):
    """No test here depends on the server installed on this machine."""
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")


def state(version="27.0.0", projects=(COPY,)):
    return {"version": version, "build": "88",
            "projects": [{"name": n, "path": p, "active": a} for n, p, a in projects],
            "active_sequence": None, "playhead_ticks": None, "selection": []}


def beat(protocol=1, schema=1, receipt=1, run_state="running", age_s=0.5):
    return Heartbeat(protocol_version=protocol, command_schema_version=schema, receipt_version=receipt,
                     state=run_state, age_s=age_s)


# ---------- check_bridge ----------
NO_PANEL = "Premiere is not running its MCP panel. Open Window > Extensions > MCP for Adobe Premiere Pro."
STALE = ("The MCP panel in Premiere does not answer: its last heartbeat is 10 s old. A dialog in Premiere "
         "may block it: close the dialog. If no dialog is open, open Window > Extensions > MCP for Adobe Premiere Pro.")


@pytest.mark.parametrize("heartbeat, message", [(None, NO_PANEL), (beat(age_s=10), STALE),
                                                (beat(run_state="stopped"), NO_PANEL)],
                         ids=["no-heartbeat", "stale", "not-running"])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("allow_untested", [False, True])
def test_bridge_down(heartbeat, message, write, allow_untested):
    with pytest.raises(CliError) as e:
        guard.check_bridge(heartbeat, "1.25.0", write=write, allow_untested=allow_untested)
    assert e.value.code == "bridge_down"
    assert e.value.message == message


@pytest.mark.parametrize("write", [False, True])
def test_tested_bridge_passes(write):
    assert guard.check_bridge(beat(), "1.25.0", write=write, allow_untested=False) == []


@pytest.mark.parametrize("heartbeat, panel", [(beat(), "1.26.0"),
                                              (beat(), None),
                                              (beat(protocol=2), "1.25.0"),
                                              (beat(schema=2), "1.25.0"),
                                              (beat(protocol=2, schema=2), "1.26.0")],
                         ids=["panel-1.26.0", "panel-unknown", "protocol-2", "schema-2", "everything-new"])
def test_untested_panel(heartbeat, panel):
    read = guard.check_bridge(heartbeat, panel, write=False, allow_untested=False)
    assert len(read) == 1
    with pytest.raises(CliError) as e:
        guard.check_bridge(heartbeat, panel, write=True, allow_untested=False)
    assert e.value.code == "untested_bridge"
    assert "--allow-untested" in e.value.message
    allowed = guard.check_bridge(heartbeat, panel, write=True, allow_untested=True)
    assert len(allowed) == 1 and "--allow-untested" in allowed[0]
    # on a read the override changes nothing: still the same single warning
    assert len(guard.check_bridge(heartbeat, panel, write=False, allow_untested=True)) == 1


WAITING = ("The panel is open but its connector is stopped. "
           "Click Start Bridge in the MCP Bridge panel.")


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("allow_untested", [False, True])
def test_waiting_heartbeat_is_bridge_down(write, allow_untested):
    with pytest.raises(CliError) as e:
        guard.check_bridge(beat(run_state="waiting"), "1.25.0", write=write, allow_untested=allow_untested)
    assert e.value.code == "bridge_down"
    assert e.value.message == WAITING and "Start Bridge" in e.value.message


def test_an_old_waiting_heartbeat_is_the_stale_message():
    with pytest.raises(CliError) as e:
        guard.check_bridge(beat(run_state="waiting", age_s=10), "1.25.0", write=True, allow_untested=False)
    assert e.value.code == "bridge_down" and e.value.message == STALE


def test_untested_server_warns_a_read_and_refuses_a_write():
    read = guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=False, server="1.26.0")
    assert len(read) == 1 and "1.26.0" in read[0] and "1.25.0" in read[0]
    with pytest.raises(CliError) as e:
        guard.check_bridge(beat(), "1.25.0", write=True, allow_untested=False, server="1.26.0")
    assert e.value.code == "untested_bridge"
    assert "1.26.0" in e.value.message and "--allow-untested" in e.value.message
    allowed = guard.check_bridge(beat(), "1.25.0", write=True, allow_untested=True, server="1.26.0")
    assert len(allowed) == 1 and "1.26.0" in allowed[0] and "--allow-untested" in allowed[0]
    assert guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=True, server="1.26.0") == read


def test_missing_server_refuses_a_write():
    read = guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=False, server=None)
    assert len(read) == 1 and "unknown" in read[0]
    with pytest.raises(CliError) as e:
        guard.check_bridge(beat(), "1.25.0", write=True, allow_untested=False, server=None)
    assert e.value.code == "untested_bridge" and "unknown" in e.value.message
    allowed = guard.check_bridge(beat(), "1.25.0", write=True, allow_untested=True, server=None)
    assert len(allowed) == 1 and "--allow-untested" in allowed[0]


def test_a_tested_server_adds_nothing():
    assert guard.check_bridge(beat(), "1.25.0", write=True, allow_untested=False, server="1.25.0") == []


def test_every_untested_version_is_named_by_one_error():
    heartbeat = beat(protocol=2)
    with pytest.raises(CliError) as e:
        guard.check_bridge(heartbeat, "1.26.0", write=True, allow_untested=False, server="1.27.0")
    assert e.value.code == "untested_bridge"
    for part in ("protocol 2", "panel version 1.26.0", "1.27.0"):
        assert part in e.value.message
    # a read gets one warning for the panel side and one for the server, an allowed write the same two
    read = guard.check_bridge(heartbeat, "1.26.0", write=False, allow_untested=False, server="1.27.0")
    assert len(read) == 2 and "protocol 2" in read[0] and "1.27.0" in read[1]
    allowed = guard.check_bridge(heartbeat, "1.26.0", write=True, allow_untested=True, server="1.27.0")
    assert len(allowed) == 2 and all("--allow-untested" in w for w in allowed)


def test_unset_server_is_looked_up_when_called(monkeypatch):
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.26.0")
    warnings = guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=False)
    assert len(warnings) == 1 and "1.26.0" in warnings[0]
    # a value given by the caller wins, None included
    assert guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=False, server="1.25.0") == []
    assert len(guard.check_bridge(beat(), "1.25.0", write=False, allow_untested=False, server=None)) == 1


@pytest.mark.parametrize("heartbeat", [None, beat(age_s=10), beat(run_state="waiting"), beat(run_state="stopped")],
                         ids=["none", "stale", "waiting", "stopped"])
def test_a_down_bridge_never_looks_up_the_server(heartbeat, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the server was looked up")

    monkeypatch.setattr(guard, "server_version", boom)
    with pytest.raises(CliError) as e:
        guard.check_bridge(heartbeat, "1.25.0", write=False, allow_untested=False)
    assert e.value.code == "bridge_down"


# ---------- server_version ----------
def install(tmp_path, package=None):
    """A fake npm install: bin/premiere-pro-mcp is a symlink to lib/premiere-pro-mcp/dist/index.js."""
    root = tmp_path / "lib" / "premiere-pro-mcp"
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "index.js").write_text("#!/usr/bin/env node\n")
    if package is not None:
        (root / "package.json").write_text(package)
    (tmp_path / "bin").mkdir()
    link = tmp_path / "bin" / "premiere-pro-mcp"
    link.symlink_to("../lib/premiere-pro-mcp/dist/index.js")
    return link


def test_server_version_reads_package_json(tmp_path):
    link = install(tmp_path, json.dumps({"name": "premiere-pro-mcp", "version": "1.25.0"}))
    asked = []

    def which(name):
        asked.append(name)
        return str(link)

    assert REAL_SERVER_VERSION(which=which) == "1.25.0"
    assert asked == ["premiere-pro-mcp"]


def test_server_version_missing_parts_give_none(tmp_path):
    assert REAL_SERVER_VERSION(which=lambda name: None) is None                                # no binary
    assert REAL_SERVER_VERSION(which=lambda name: str(tmp_path / "gone")) is None              # binary not on disk
    assert REAL_SERVER_VERSION(which=lambda name: str(install(tmp_path))) is None             # no package.json


@pytest.mark.parametrize("package", [
    json.dumps({"name": "other-server", "version": "1.25.0"}),            # other package name
    json.dumps({"version": "1.25.0"}),                                    # no name
    json.dumps({"name": "premiere-pro-mcp"}),                             # no version
    json.dumps({"name": "premiere-pro-mcp", "version": 1.25}),            # version is not a string
    json.dumps({"name": "premiere-pro-mcp", "version": ""}),              # empty version
    json.dumps(["premiere-pro-mcp", "1.25.0"]),                           # not an object
    "{not json",
    "",
], ids=["other-name", "no-name", "no-version", "number", "empty", "list", "bad-json", "empty-file"])
def test_server_version_unusable_package_json_gives_none(tmp_path, package):
    link = install(tmp_path, package)
    assert REAL_SERVER_VERSION(which=lambda name: str(link)) is None


def test_server_version_unreadable_package_json_gives_none(tmp_path):
    link = install(tmp_path)
    (tmp_path / "lib" / "premiere-pro-mcp" / "package.json").mkdir()                          # a folder
    assert REAL_SERVER_VERSION(which=lambda name: str(link)) is None
    folder = tmp_path / "lib" / "premiere-pro-mcp" / "package.json"
    folder.rmdir()
    folder.write_bytes(b"\xff\xfe not utf-8")
    assert REAL_SERVER_VERSION(which=lambda name: str(link)) is None


# ---------- check_state: Premiere version ----------
def test_write_on_premiere_26_is_refused():
    with pytest.raises(CliError) as e:
        guard.check_state(state(version="26.5.2"), write=True)
    assert e.value.code == "untested_premiere" and "26.5.2" in e.value.message
    assert e.value.message == "Live writes support Premiere 27.0 only. Running: 26.5.2."
    assert guard.check_state(state(version="26.5.2"), write=False)            # one warning, no raise
    assert len(guard.check_state(state(version="26.5.2"), write=False)) == 1


def test_untested_premiere_is_allowed_with_the_override():
    w = guard.check_state(state(version="26.5.2"), write=True, allow_untested=True)
    assert len(w) == 1 and "26.5.2" in w[0] and "--allow-untested" in w[0]


@pytest.mark.parametrize("version", ["27.0.0", "27.0.3"])
def test_tested_premiere_versions_pass(version):
    assert guard.check_state(state(version=version), write=True) == []
    assert guard.check_state(state(version=version), write=False) == []


@pytest.mark.parametrize("version", [None, "", "28.0.0"])
def test_missing_or_new_premiere_version_is_untested(version):
    assert len(guard.check_state(state(version=version), write=False)) == 1
    with pytest.raises(CliError) as e:
        guard.check_state(state(version=version), write=True)
    assert e.value.code == "untested_premiere"


# ---------- check_state: project guard ----------
@pytest.mark.parametrize("name", ["20251227 Sno Hamptons Claude 2.prproj", "20260523 Claude tests.prproj",
                                  "20261115 Fall Claude_1.prproj", "20261006 Ander home dinner claude.prproj"])
def test_working_copy_names_pass_the_project_guard(name):
    assert guard.check_state(state(projects=((name, "/p/" + name, True),)), write=True) == []


def test_the_rule_is_contains_claude_in_any_case():
    # No word-boundary logic: "Claudette" contains the word, so it passes.
    name = "Claudette holiday.prproj"
    assert guard.check_state(state(projects=((name, "/p/" + name, True),)), write=True) == []
    name = "CLAUDE final.prproj"
    assert guard.check_state(state(projects=((name, "/p/" + name, True),)), write=True) == []


def test_own_project_is_refused_and_needs_both_flags():
    s = state(projects=(("Family film.prproj", "/p/Family film.prproj", True),))
    for kwargs in ({}, {"any_project": True}, {"project": "/p/Family film.prproj"}):
        with pytest.raises(CliError) as e:
            guard.check_state(s, write=True, **kwargs)
        assert e.value.code == "project_guard"
    w = guard.check_state(s, write=True, any_project=True, project="/p/Family film.prproj")
    assert any("overridden" in x for x in w)


def test_project_guard_message_names_the_project():
    with pytest.raises(CliError) as e:
        guard.check_state(state(projects=(OWN,)), write=True)
    assert e.value.message == "This is not a Claude working copy: Family film.prproj."


def test_project_guard_gives_no_warning_on_a_read():
    assert guard.check_state(state(projects=(OWN,)), write=False) == []
    assert guard.check_state(state(projects=(OWN,)), write=False, any_project=True,
                             project="/p/Family film.prproj") == []


def test_override_is_reported_only_when_it_was_needed():
    assert guard.check_state(state(), write=True, any_project=True, project="/p/Fall Claude.prproj") == []
    w = guard.check_state(state(projects=(OWN,)), write=True, any_project=True, project="/p/Family film.prproj")
    assert len(w) == 1 and w[0].startswith("project guard overridden")


def test_guard_checks_the_file_name_not_the_folder():
    s = state(projects=(("Family film.prproj", "/Users/u/Claude/Family film.prproj", True),))
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True)
    assert e.value.code == "project_guard"
    assert e.value.message == "This is not a Claude working copy: Family film.prproj."


def test_guard_falls_back_to_the_name_when_the_path_is_empty():
    assert guard.check_state(state(projects=(("Fall Claude.prproj", "", True),)), write=True) == []
    assert guard.check_state(state(projects=(("Fall Claude.prproj", None, True),)), write=True) == []
    with pytest.raises(CliError) as e:
        guard.check_state(state(projects=(("Family film.prproj", "", True),)), write=True)
    assert e.value.code == "project_guard"


def test_any_project_cannot_override_with_a_path_of_another_project():
    # One project open and --project names a different path: the mismatch wins over the override.
    with pytest.raises(CliError) as e:
        guard.check_state(state(projects=(OWN,)), write=True, any_project=True, project="/p/Other.prproj")
    assert e.value.code == "project_mismatch"


def test_a_single_project_with_no_active_flag_counts_as_active():
    own = (("Family film.prproj", "/p/Family film.prproj", False),)
    copy = (("Fall Claude.prproj", "/p/Fall Claude.prproj", False),)
    with pytest.raises(CliError) as e:
        guard.check_state(state(projects=own), write=True)
    assert e.value.code == "project_guard"
    assert guard.check_state(state(projects=copy), write=True) == []
    assert guard.check_state(state(projects=copy), write=True, project="/p/Fall Claude.prproj") == []


# ---------- check_state: how many projects ----------
TWO = (("Fall Claude.prproj", "/p/Fall Claude.prproj", True),
       ("Other Claude.prproj", "/p/Other Claude.prproj", False))


def test_two_projects_need_the_project_path():
    s = state(projects=TWO)
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True)
    assert e.value.code == "two_projects"
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True, project="/p/Other Claude.prproj")
    assert e.value.code == "project_mismatch"
    assert guard.check_state(s, write=True, project="/p/Fall Claude.prproj") == []
    # a read warns in the first two cases and stays quiet when the path is right
    assert len(guard.check_state(s, write=False)) == 1
    assert len(guard.check_state(s, write=False, project="/p/Other Claude.prproj")) == 1
    assert guard.check_state(s, write=False, project="/p/Fall Claude.prproj") == []


def test_two_projects_with_none_marked_active_refuse_a_write():
    s = state(projects=(("A Claude.prproj", "/p/A Claude.prproj", False),
                        ("B Claude.prproj", "/p/B Claude.prproj", False)))
    for kwargs in ({}, {"project": "/p/A Claude.prproj"}):
        with pytest.raises(CliError) as e:
            guard.check_state(s, write=True, **kwargs)
        assert e.value.code == "two_projects"
        assert len(guard.check_state(s, write=False, **kwargs)) == 1


def test_two_projects_the_guard_looks_at_the_active_one():
    s = state(projects=(("Family film.prproj", "/p/Family film.prproj", True),
                        ("Other Claude.prproj", "/p/Other Claude.prproj", False)))
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True, project="/p/Family film.prproj")
    assert e.value.code == "project_guard"


def test_one_project_with_a_different_project_path():
    s = state()
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True, project="/p/Other.prproj")
    assert e.value.code == "project_mismatch"
    assert len(guard.check_state(s, write=False, project="/p/Other.prproj")) == 1
    assert guard.check_state(s, write=True, project="/p/Fall Claude.prproj") == []
    assert guard.check_state(s, write=False, project="/p/Fall Claude.prproj") == []


def test_a_project_path_is_never_the_same_as_an_unknown_active_path():
    s = state(projects=(("Fall Claude.prproj", "", True),))
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True, project=".")
    assert e.value.code == "project_mismatch"


def test_paths_are_compared_after_normpath():
    s = state()
    assert guard.check_state(s, write=True, project="/p//x/../Fall Claude.prproj") == []
    two = state(projects=TWO)
    assert guard.check_state(two, write=True, project="/p/./Fall Claude.prproj") == []


def test_no_project_refuses_a_write_only():
    s = state(projects=())
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True)
    assert e.value.code == "no_project"
    with pytest.raises(CliError) as e:
        guard.check_state(s, write=True, project="/p/Fall Claude.prproj", any_project=True)
    assert e.value.code == "no_project"
    assert guard.check_state(s, write=False) == []
    assert guard.check_state(s, write=False, project="/p/Fall Claude.prproj") == []


def test_no_project_with_an_untested_premiere_warns_on_a_read():
    assert len(guard.check_state(state(version="26.5.2", projects=()), write=False)) == 1


# ---------- check_recovery ----------
def test_recovery_blocks_writes(bridge_dir):  # noqa: F811
    assert guard.check_recovery(bridge_dir, write=True) == []
    assert guard.check_recovery(bridge_dir, write=False) == []
    mark_recovery(bridge_dir, "unknown_command_outcome")
    with pytest.raises(CliError) as e:
        guard.check_recovery(bridge_dir, write=True)
    assert e.value.code == "recovery_required"
    assert e.value.message == "A recovery is open. Run: cli-anything-premiere recovery show"
    assert len(guard.check_recovery(bridge_dir, write=False)) == 1


def test_an_unreadable_recovery_marker_still_blocks_writes(bridge_dir):  # noqa: F811
    (bridge_dir / RECOVERY_FILE).write_text("not json", encoding="utf-8")
    with pytest.raises(CliError) as e:
        guard.check_recovery(bridge_dir, write=True)
    assert e.value.code == "recovery_required"
    assert len(guard.check_recovery(bridge_dir, write=False)) == 1


# ---------- panel_version ----------
def manifest(tmp_path, text):
    path = tmp_path / "manifest.xml"
    path.write_text(text, encoding="utf-8")
    return path


def test_panel_version_reads_the_manifest(tmp_path):
    path = manifest(tmp_path, '<?xml version="1.0" encoding="UTF-8"?>\n'
                              '<ExtensionManifest Version="7.0" ExtensionBundleId="com.mcp.premiere.bridge" '
                              'ExtensionBundleVersion="1.25.0" ExtensionBundleName="MCP">\n'
                              '  <ExtensionList><Extension Id="x" Version="9.9.9"/></ExtensionList>\n'
                              '</ExtensionManifest>')
    assert guard.panel_version(path) == "1.25.0"
    assert guard.panel_version(tmp_path / "missing.xml") is None


def test_panel_version_is_none_when_the_manifest_cannot_be_used(tmp_path):
    assert guard.panel_version(manifest(tmp_path, "<ExtensionManifest")) is None            # not well formed
    assert guard.panel_version(manifest(tmp_path, "")) is None                              # empty
    assert guard.panel_version(manifest(tmp_path, "<ExtensionManifest Version='7.0'/>")) is None   # no attribute
    assert guard.panel_version(manifest(tmp_path, "<ExtensionManifest ExtensionBundleVersion=''/>")) is None
    assert guard.panel_version(tmp_path) is None                                            # a folder, not a file


def test_panel_version_refuses_entity_tricks(tmp_path):
    bomb = ('<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "1.25.0">]>'
            '<ExtensionManifest ExtensionBundleVersion="&a;"/>')
    assert guard.panel_version(manifest(tmp_path, bomb)) is None


def test_default_manifest_path_is_the_installed_panel():
    assert guard.PANEL_MANIFEST.parts[-3:] == ("MCPBridgeCEP", "CSXS", "manifest.xml")


# ---------- final review fixes: A3 ----------
@pytest.mark.parametrize("shape", MARKER_SHAPES)
def test_a_marker_that_cannot_be_read_blocks_a_write(bridge_dir, shape):  # noqa: F811
    unreadable_marker(bridge_dir, shape)
    try:
        with pytest.raises(CliError) as e:
            guard.check_recovery(bridge_dir, write=True)
        warnings = guard.check_recovery(bridge_dir, write=False)
    finally:
        restore_marker_mode(bridge_dir)
    assert e.value.code == "recovery_required"
    assert len(warnings) == 1


# ---------- final review fixes: A2 ----------
def test_active_project_and_file_name_are_public():
    copy = {"name": "Fall Claude.prproj", "path": "/p/Fall Claude.prproj", "active": True}
    other = {"name": "B.prproj", "path": "/p/B.prproj", "active": False}
    assert guard.active_project([other, copy]) is copy
    assert guard.active_project([{**other}]) == other                 # the only project counts as active
    assert guard.active_project([other, {**copy, "active": False}]) is None
    assert guard.active_project(["not a dict", copy]) is copy          # a bad entry is skipped, not a crash
    assert guard.active_project(["not a dict"]) is None
    assert guard.active_project([]) is None
    assert guard.project_file_name(copy) == "Fall Claude.prproj"
    assert guard.project_file_name({"name": "N.prproj", "path": ""}) == "N.prproj"
    assert guard.project_file_name({"name": "N.prproj", "path": "/a/Claude/x.prproj"}) == "x.prproj"


# ---------- final review fixes: A6 (pins behavior the recipes lean on) ----------
@pytest.mark.parametrize("version", ["27.1.0", "27.10.0", "27.0"])
def test_versions_near_the_tested_one_are_refused(version):
    with pytest.raises(CliError) as e:
        guard.check_state(state(version=version), write=True)
    assert e.value.code == "untested_premiere" and version in e.value.message
    assert len(guard.check_state(state(version=version), write=False)) == 1


OWN_ACTIVE_TWO = (("Family film.prproj", "/p/Family film.prproj", True),
                  ("Fall Claude.prproj", "/p/Fall Claude.prproj", False))


@pytest.mark.parametrize("kwargs, code", [
    ({}, "two_projects"),
    ({"any_project": True}, "two_projects"),
    ({"project": "/p/Fall Claude.prproj"}, "project_mismatch"),
    ({"project": "/p/Fall Claude.prproj", "any_project": True}, "project_mismatch"),
    ({"project": "/p/Family film.prproj"}, "project_guard"),
])
def test_two_projects_with_the_owners_film_active_refuse_a_write(kwargs, code):
    with pytest.raises(CliError) as e:
        guard.check_state(state(projects=OWN_ACTIVE_TWO), write=True, **kwargs)
    assert e.value.code == code


def test_two_projects_with_the_owners_film_active_pass_only_with_both_flags():
    w = guard.check_state(state(projects=OWN_ACTIVE_TWO), write=True, any_project=True,
                          project="/p/Family film.prproj")
    assert len(w) == 1 and w[0].startswith("project guard overridden")


@pytest.mark.parametrize("projects, kwargs, code", [
    ((OWN,), {}, "project_guard"),
    (OWN_ACTIVE_TWO, {}, "two_projects"),
    ((), {}, "no_project"),
    ((), {"project": "/p/Fall Claude.prproj", "any_project": True}, "no_project"),
])
@pytest.mark.parametrize("version", ["27.0.0", "26.5.2"])
def test_allow_untested_does_not_lift_the_project_checks(projects, kwargs, code, version):
    with pytest.raises(CliError) as e:
        guard.check_state(state(version=version, projects=projects), write=True, allow_untested=True, **kwargs)
    assert e.value.code == code
