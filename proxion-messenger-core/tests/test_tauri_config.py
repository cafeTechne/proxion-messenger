"""Static checks on the Tauri wrapper's IPC surface, allowlist and CSP.

Plain file reads only: keeps the desktop shell's exposed commands in sync with
what the web client actually calls, and pins the allowlist/CSP hardening.
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_WEB = os.path.join(_ROOT, "web")
_TAURI = os.path.join(_ROOT, "tauri-app", "src-tauri")
_MAIN_RS = os.path.join(_TAURI, "src", "main.rs")
_CONF = os.path.join(_TAURI, "tauri.conf.json")

_REPO_URL_RE = r"^https://github\.com/cafeTechne/proxion-messenger"

# Registered commands with no web caller, each with a reason.
_UNUSED_OK = {
    "quit_app": "tray 'quit' menu item handles exit in Rust; kept for IPC parity",
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _web_sources():
    for name in sorted(os.listdir(_WEB)):
        if name.endswith(".js") and not name.endswith(".test.js"):
            yield name, _read(os.path.join(_WEB, name))


def _invoked_commands():
    pat = re.compile(r"""\binvoke\(\s*(['"])([^'"]+)\1""")
    found = {}
    for name, src in _web_sources():
        for m in pat.finditer(src):
            cmd = m.group(2)
            if cmd.startswith("plugin:"):
                continue
            found.setdefault(cmd, name)
    return found


def _registered_commands():
    src = _read(_MAIN_RS)
    m = re.search(r"generate_handler!\[(.*?)\]", src, re.S)
    assert m, "generate_handler![...] not found in main.rs"
    return {c.strip() for c in m.group(1).split(",") if c.strip()}


def _conf():
    return json.loads(_read(_CONF))


def _about_url():
    src = _read(os.path.join(_WEB, "main.js"))
    m = re.search(
        r"#settings-about-website-btn'.*?const url = '([^']+)'", src, re.S
    )
    assert m, "About website handler not found in web/main.js"
    return m.group(1)


def test_every_invoked_command_is_registered():
    registered = _registered_commands()
    invoked = _invoked_commands()
    assert invoked, "no Tauri invoke() calls found; regex likely stale"
    missing = {c: f for c, f in invoked.items() if c not in registered}
    assert not missing, f"invoked but not registered in main.rs: {missing}"


def test_every_registered_command_is_used():
    registered = _registered_commands()
    invoked = set(_invoked_commands())
    unused = registered - invoked - set(_UNUSED_OK)
    assert not unused, f"registered but never invoked by web/ (dead IPC): {sorted(unused)}"
    stale = set(_UNUSED_OK) - registered
    assert not stale, f"_UNUSED_OK lists unregistered commands: {sorted(stale)}"


def test_no_pod_credential_readback():
    src = _read(_MAIN_RS)
    assert "load_pod_credentials" not in src


def _walk_all_flags(node, path="allowlist"):
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{path}.{k}"
            if k == "all":
                yield sub, v
            yield from _walk_all_flags(v, sub)


def test_allowlist_is_minimal():
    allow = _conf()["tauri"]["allowlist"]
    assert allow["all"] is False
    for path, val in _walk_all_flags(allow):
        if path == "allowlist.notification.all":
            assert val is True
        else:
            assert val is False, f"{path} must be false"

    shell = allow["shell"]
    assert shell.get("sidecar") is False
    assert isinstance(shell.get("open"), str), "shell.open must be a scoped regex"
    assert shell["open"].startswith(_REPO_URL_RE)
    assert allow["process"] == {"relaunch": True}

    # Nothing else may be switched on.
    expected = {"all", "notification", "process", "shell"}
    assert set(allow) == expected, f"unexpected allowlist keys: {set(allow) - expected}"
    assert set(shell) <= {"all", "sidecar", "open"}


def test_about_url_matches_shell_open_scope():
    scope = _conf()["tauri"]["allowlist"]["shell"]["open"]
    url = _about_url()
    assert re.match(scope, url), f"{url!r} not allowed by shell.open {scope!r}"


def test_csp_script_src_and_frame_ancestors():
    csp = _conf()["tauri"]["security"]["csp"]
    directives = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    script = directives.get("script-src")
    assert script is not None, "CSP has no script-src"
    assert "'self'" in script
    assert "'unsafe-inline'" not in script
    assert "'unsafe-eval'" not in script
    assert directives.get("frame-ancestors") == ["'none'"]
