"""Whitelist enforcement matrix (P1/P2, SC4-by-construction §19)."""
import pytest

from jarvis.actions import registry
from jarvis.actions.registry import ResolvedAction, ToolRejected
from jarvis.bus import ToolCall


def call(tool, **args):
    return ToolCall(name=tool, args={k: str(v) for k, v in args.items()})


# --- unknown tool / injection attempts ----------------------------------- #

def test_unknown_tool_rejected():
    with pytest.raises(ToolRejected):
        registry.resolve(call("delete_everything"))


def test_run_command_rejects_anything_not_whitelisted():
    for evil in ("rm -rf /", "cat /etc/shadow", "ls; rm x", "bash", "sudo df", "df -h && rm"):
        with pytest.raises(ToolRejected):
            registry.resolve(call("run_command", name=evil))


def test_run_command_maps_name_to_fixed_argv_never_model_args():
    action = registry.resolve(call("run_command", name="df"))
    assert action.argv == ("df", "-h")  # model picked a NAME; argv is ours


def test_open_app_rejects_non_whitelisted():
    for evil in ("xterm", "bash", "firefox; rm x", "FIREFOX --evil"):
        with pytest.raises(ToolRejected):
            registry.resolve(call("open_app", name=evil))


def test_open_app_accepts_whitelisted_installed():
    import shutil

    if shutil.which("firefox") is None:
        pytest.skip("firefox not installed")
    action = registry.resolve(call("open_app", name="firefox"))
    assert action.argv == ("firefox",)


# --- path containment ------------------------------------------------------ #

def test_list_dir_escapes_are_contained():
    for evil in ("/etc", "../../etc", "~/../../etc", "/root", "~/../other-user"):
        with pytest.raises(ToolRejected):
            registry.resolve(call("list_dir", path=evil))


def test_list_dir_home_paths_allowed():
    action = registry.resolve(call("list_dir", path="~"))
    assert action.tool == "list_dir"
    action = registry.resolve(call("list_dir", path="Documents"))
    assert action.argv is not None and "Documents" in action.argv[-1]


def test_list_dir_dotdot_inside_home_is_fine():
    action = registry.resolve(call("list_dir", path="~/Documents/.."))
    assert action.argv is not None


# --- set_avatar closed set ------------------------------------------------- #

def test_set_avatar_unknown_pack_rejected():
    with pytest.raises(ToolRejected) as e:
        registry.resolve(call("set_avatar", name="dragon"), installed_packs=("cat", "axolotl"))
    assert "cat" in e.value.spoken  # speaks what it DOES have


def test_set_avatar_known_pack_becomes_avatar_command():
    action = registry.resolve(call("set_avatar", name="cat"), installed_packs=("cat",))
    assert action == ResolvedAction(tool="set_avatar", avatar_command=("set_pack", "cat"))
    assert action.argv is None  # never a subprocess (NFR5)


# --- misc ------------------------------------------------------------------ #

def test_check_disk_fixed_argv():
    assert registry.resolve(call("check_disk")).argv == ("df", "-h", "/")


def test_tool_specs_enums_match_whitelists():
    from jarvis import config

    cfg = config.get()["tools"]
    specs = {s["function"]["name"]: s for s in registry.tool_specs()}
    assert set(specs) == {
        "open_app", "list_dir", "check_disk", "run_command", "describe_screen", "set_avatar",
    }
    app_enum = specs["open_app"]["function"]["parameters"]["properties"]["name"]["enum"]
    assert set(app_enum) == set(cfg["app_whitelist"])
