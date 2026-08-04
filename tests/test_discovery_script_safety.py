"""Safety guarantees for scripts/discover_node1_services.sh.

The discovery script captures the operator's real private topology, so its
handling rules are the whole point of it: the inventory goes to a file and
never to stdout, the file is owner-only (0600), and credential-shaped URL
material is redacted on the way in.

These tests never invoke the real host tools. Every command the script probes
(``tailscale``, ``docker``, ``ss``, ``nginx``, ``apachectl``, ``systemctl``,
...) is replaced by a synthetic stub on a temporary ``PATH``, and every stub
emits only reserved ``.invalid`` example data. Nothing here reads, or can
read, the machine this suite runs on.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[1]
SCRIPT = REPO_ROOT / "scripts" / "discover_node1_services.sh"

# Reserved example data only (RFC 2606 ".invalid"). These strings stand in for
# what the real tools would print on a deployment host.
STUB_HOST = "node1.example.invalid"
STUB_SECRET = "sup3rs3cret-value"

# Each stub prints fixed synthetic output. ``$@`` is ignored on purpose: the
# stub must behave identically whatever arguments the script passes.
STUBS: dict[str, str] = {
    "tailscale": (
        f"https://{STUB_HOST}/dash proxy http://127.0.0.1:8080\n"
        f"https://{STUB_HOST}/grafana?token={STUB_SECRET} proxy http://127.0.0.1:3000\n"
    ),
    "docker": (
        f"portal  ghcr.io/example/portal  0.0.0.0:8443->8443/tcp  Up\n"
        f"# env URL=https://admin:{STUB_SECRET}@{STUB_HOST}/api\n"
    ),
    "ss": "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*\n",
    # ``netstat`` is the script's fallback when ``ss`` is absent. It is stubbed
    # too, so no code path can reach the host's real socket table.
    "netstat": "LISTEN 0 4096 127.0.0.1:9090 0.0.0.0:*\n",
    "nginx": (
        f"server_name {STUB_HOST};\n"
        f"proxy_pass https://svc.example.invalid/?access_token={STUB_SECRET};\n"
        "stub_status on;\n"
    ),
    "apachectl": f"VirtualHost {STUB_HOST}:443\n",
    "systemctl": "ollama.service loaded active running Ollama\n",
}


def assert_owner_only_mode(path: Path) -> None:
    """Assert ``path`` is owner-only (0o600) on platforms with POSIX modes.

    On Windows the script still runs ``chmod 600`` under Git Bash, but NTFS
    ACLs are the real permission model there: ``os.stat`` synthesises mode
    bits (typically 0o666) rather than reporting POSIX permissions, so the
    check is meaningless and is omitted. Every other assertion still runs.
    """
    if os.name == "nt":
        return
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def write_stub_path(tmp_path: Path) -> Path:
    """Build a PATH directory of synthetic stubs plus the few real utilities.

    The script legitimately needs ``sed``, ``grep``, ``head``, ``dirname``,
    ``mkdir``, ``mv``, ``chmod``, ``rm`` and ``cat``. Those are symlinked from
    the system; every *discovery* tool is a stub, so no real topology can be
    read even if the host happens to run tailscale or docker.
    """

    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()

    for name, output in STUBS.items():
        stub = stub_dir / name
        stub.write_text(
            "#!/bin/sh\n"
            "# synthetic stub: ignores arguments, prints fixed example data\n"
            f"printf '%s' '{output}'\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    for utility in (
        "sh", "sed", "grep", "head", "dirname", "mkdir", "mv", "chmod",
        "rm", "cat", "printf", "command", "umask", "uname", "sort", "tr",
    ):
        resolved = shutil.which(utility)
        if resolved:
            (stub_dir / utility).symlink_to(resolved)

    return stub_dir


# Utilities the script itself needs; never a discovery tool.
SUPPORT_UTILITIES = (
    "sh", "sed", "grep", "head", "dirname", "mkdir", "mv", "chmod", "rm",
    "cat", "sort", "tr",
)


def write_bare_path(tmp_path: Path) -> Path:
    """A PATH with the support utilities but no discovery tool at all."""

    bare = tmp_path / "barebin"
    bare.mkdir()
    for utility in SUPPORT_UTILITIES:
        resolved = shutil.which(utility)
        if resolved:
            (bare / utility).symlink_to(resolved)

    return bare


def write_failing_stub(stub_dir: Path, name: str, output: str, status: int) -> None:
    """Replace one stub with a synthetic *failing* probe.

    The stub prints reserved example data on both streams and exits nonzero,
    standing in for the real-world "installed but broken / needs root" case.
    """

    stub = stub_dir / name
    stub.write_text(
        "#!/bin/sh\n"
        "# synthetic failing stub: prints example data, then fails\n"
        f"printf '%s\\n' '{output}'\n"
        f"printf '%s\\n' '{output}' >&2\n"
        f"exit {status}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def run_script(
    tmp_path: Path,
    *args: str,
    home: Path | None = None,
    state_home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the discovery script against stubs only, in a temporary HOME."""

    stub_dir = write_stub_path(tmp_path)
    sandbox_home = home or (tmp_path / "home")
    sandbox_home.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": str(stub_dir),
        "HOME": str(sandbox_home),
        "SHELL": "/bin/sh",
    }
    if state_home is not None:
        env["XDG_STATE_HOME"] = str(state_home)

    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_script_is_syntactically_valid() -> None:
    result = subprocess.run(
        ["sh", "-n", str(SCRIPT)], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_explicit_output_path_is_written_at_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "inventory" / "services.txt"

    result = run_script(tmp_path, "--output", str(target))

    assert result.returncode == 0, result.stderr
    assert target.is_file()
    assert_owner_only_mode(target)
    assert target.read_text(encoding="utf-8").strip()


def test_output_equals_form_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "explicit.txt"

    result = run_script(tmp_path, f"--output={target}")

    assert result.returncode == 0, result.stderr
    assert_owner_only_mode(target)


def test_default_output_path_follows_xdg_state_home(tmp_path: Path) -> None:
    state_home = tmp_path / "state"

    result = run_script(tmp_path, state_home=state_home)

    expected = state_home / "agentic-harness" / "services-inventory.txt"
    assert result.returncode == 0, result.stderr
    assert expected.is_file()
    assert_owner_only_mode(expected)


def test_default_output_falls_back_to_home_local_state(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = run_script(tmp_path, home=home)

    expected = home / ".local" / "state" / "agentic-harness" / "services-inventory.txt"
    assert result.returncode == 0, result.stderr
    assert expected.is_file()
    assert_owner_only_mode(expected)


def test_no_topology_is_printed_to_stdout(tmp_path: Path) -> None:
    """The inventory goes to the file; stdout carries only the handling notice."""

    target = tmp_path / "inventory.txt"

    result = run_script(tmp_path, "--output", str(target))

    assert result.returncode == 0, result.stderr
    contents = target.read_text(encoding="utf-8")
    # The stub topology really did reach the file...
    assert STUB_HOST in contents
    # ...and none of it reached either standard stream.
    assert STUB_HOST not in result.stdout
    assert STUB_HOST not in result.stderr
    assert "127.0.0.1:8080" not in result.stdout
    assert "ollama" not in result.stdout.lower()
    assert "=====" not in result.stdout
    # stdout is the handling instructions plus the path, nothing more.
    assert str(target) in result.stdout
    assert "mode 0600" in result.stdout
    assert "Do not commit it" in result.stdout


def test_sensitive_url_query_values_are_redacted_in_the_file(tmp_path: Path) -> None:
    target = tmp_path / "inventory.txt"

    result = run_script(tmp_path, "--output", str(target))
    contents = target.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    # The secret value is gone from every credential-shaped query parameter...
    assert STUB_SECRET not in contents
    # ...while the key names, and the surrounding topology, remain readable.
    assert "token=REDACTED" in contents
    assert "access_token=REDACTED" in contents
    assert STUB_HOST in contents


def test_url_userinfo_is_redacted_in_the_file(tmp_path: Path) -> None:
    target = tmp_path / "inventory.txt"

    result = run_script(tmp_path, "--output", str(target))
    contents = target.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    # "https://admin:secret@host" must not survive with its credentials.
    assert f"admin:{STUB_SECRET}@" not in contents
    assert "REDACTED@" in contents
    assert STUB_SECRET not in contents


@pytest.mark.parametrize(
    "key, value",
    [
        ("token", "tok-aaa"),
        ("access_token", "tok-bbb"),
        ("sig", "sig-ccc"),
        ("signature", "sig-ddd"),
        ("password", "pw-eee"),
        ("secret", "sec-fff"),
        ("credential", "cred-ggg"),
        ("api_key", "key-hhh"),
        ("api-key", "key-kkk"),
        ("x-api-key", "key-lll"),
        ("key", "key-iii"),
        ("auth", "auth-jjj"),
    ],
)
def test_each_credential_shaped_query_key_is_redacted(
    tmp_path: Path, key: str, value: str
) -> None:
    """Drive one crafted URL per key through the script's own redactor."""

    stub_dir = write_stub_path(tmp_path)
    url = f"https://{STUB_HOST}/dash?{key}={value}"
    tailscale = stub_dir / "tailscale"
    tailscale.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{url}'\n", encoding="utf-8"
    )
    tailscale.chmod(0o755)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(stub_dir), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )
    contents = target.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert value not in contents, f"{key} value survived redaction"
    assert f"{key}=REDACTED" in contents
    assert value not in result.stdout


def test_an_existing_output_is_replaced_safely(tmp_path: Path) -> None:
    """A rerun replaces stale content and re-tightens the mode."""

    target = tmp_path / "inventory.txt"
    target.write_text("STALE-PREVIOUS-INVENTORY\n", encoding="utf-8")
    target.chmod(0o644)

    result = run_script(tmp_path, "--output", str(target))
    contents = target.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    # The stale inventory is gone, not appended to.
    assert "STALE-PREVIOUS-INVENTORY" not in contents
    assert STUB_HOST in contents
    # A previously world-readable file does not stay world-readable.
    assert_owner_only_mode(target)


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "inventory.txt"

    run_script(tmp_path, "--output", str(target))

    leftovers = list(target.parent.glob("inventory.txt.tmp*"))
    assert leftovers == [], leftovers


def test_missing_tools_are_skipped_cleanly(tmp_path: Path) -> None:
    """With no discovery tool present at all, the run still succeeds."""

    bare = write_bare_path(tmp_path)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(bare), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )
    contents = target.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "not installed; skipping" in contents
    assert_owner_only_mode(target)


def test_unknown_argument_is_refused_without_writing(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--not-an-option")

    assert result.returncode == 2
    assert "unknown argument" in result.stderr
    assert not (tmp_path / "home" / ".local").exists()


def test_output_flag_requires_a_value(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--output")

    assert result.returncode == 2
    assert "--output requires a path" in result.stderr


def test_help_prints_usage_and_no_topology(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--help")

    assert result.returncode == 0
    assert "Usage: discover_node1_services.sh" in result.stdout
    assert STUB_HOST not in result.stdout
    assert "=====" not in result.stdout


DISCOVERY_TOOLS = (
    "tailscale", "docker", "ss", "netstat", "nginx", "apachectl", "systemctl",
)


def test_no_real_discovery_tool_is_reachable_from_the_stub_path(tmp_path: Path) -> None:
    """The stub PATH must shadow every host tool the script probes.

    Several of these genuinely exist on a developer or CI machine. If any one
    of them resolved outside the stub directory, this suite would be reading
    the real network topology of whatever host it runs on.
    """

    stub_dir = write_stub_path(tmp_path)

    for tool in DISCOVERY_TOOLS:
        resolved = shutil.which(tool, path=str(stub_dir))
        assert resolved is None or Path(resolved).parent == stub_dir, (tool, resolved)


def test_the_bare_path_run_cannot_reach_a_real_socket_table(tmp_path: Path) -> None:
    """The "no tools installed" case must not fall through to a real netstat."""

    bare = write_bare_path(tmp_path)

    for tool in DISCOVERY_TOOLS:
        assert shutil.which(tool, path=str(bare)) is None, tool


FAILING_STUB_HOST = "synthetic-failure.example.invalid"
# A neutral, low-entropy reserved marker: it stands in for whatever a failing
# probe prints in a credential-shaped position, and the assertions below prove
# that string never reaches a receipt and never survives into the inventory.
FAILING_STUB_MARKER = "synthetic-redaction-marker"
FAILING_STUB_OUTPUT = f"https://{FAILING_STUB_HOST}/x?token={FAILING_STUB_MARKER}"

# (stub name, probe name expected in the partial receipt).
FAILING_PROBES = (
    ("tailscale", "tailscale serve status"),
    ("docker", "docker ps"),
    ("ss", "ss -tlnp"),
    ("nginx", "nginx -T"),
    ("apachectl", "apachectl -S"),
    ("systemctl", "systemctl list-units"),
)


def run_with_failing_stub(
    tmp_path: Path, name: str, *, status: int = 7
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the script with exactly one probe replaced by a failing stub."""

    stub_dir = write_stub_path(tmp_path)
    write_failing_stub(stub_dir, name, FAILING_STUB_OUTPUT, status)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(stub_dir), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )

    return result, target


@pytest.mark.parametrize("name, probe", FAILING_PROBES)
def test_a_failing_probe_makes_the_run_fail(tmp_path: Path, name: str, probe: str) -> None:
    """An installed-but-failing probe must never read as a clean success.

    This is the direct regression: ``set -e`` inside the left side of
    ``collect | redact_urls`` was invisible, because the pipeline's status is
    sed's, so a probe exiting 7 produced exit 0 and a "Wrote inventory" receipt.
    """

    result, target = run_with_failing_stub(tmp_path, name)

    assert result.returncode != 0, result.stdout
    # No success receipt anywhere.
    assert "Wrote inventory" not in result.stdout
    assert "Wrote inventory" not in result.stderr
    # A partial receipt naming the probe that failed.
    receipt = result.stdout + result.stderr
    assert "PARTIAL" in receipt
    assert "INCOMPLETE" in receipt
    assert probe in receipt
    assert str(target) in receipt


@pytest.mark.parametrize("name, probe", FAILING_PROBES)
def test_the_partial_receipt_leaks_no_stub_output_or_marker(
    tmp_path: Path, name: str, probe: str
) -> None:
    """The receipt carries the path and probe names — nothing the probe printed."""

    result, _target = run_with_failing_stub(tmp_path, name)
    receipt = result.stdout + result.stderr

    assert FAILING_STUB_MARKER not in receipt
    assert FAILING_STUB_HOST not in receipt
    assert STUB_HOST not in receipt
    assert "=====" not in receipt
    assert "127.0.0.1" not in receipt


@pytest.mark.parametrize("name, probe", FAILING_PROBES)
def test_a_failing_probe_still_collects_the_later_sections(
    tmp_path: Path, name: str, probe: str
) -> None:
    """Collection continues past a failure: the partial inventory stays useful."""

    result, target = run_with_failing_stub(tmp_path, name)
    contents = target.read_text(encoding="utf-8")

    assert result.returncode != 0
    # Every section header is present, including all the ones after the failure.
    for header in (
        "tailscale serve status",
        "docker containers",
        "listening TCP sockets",
        "nginx virtual hosts and proxied locations",
        "apache server-status",
        "running AI/serving-related systemd services",
    ):
        assert f"===== {header}" in contents, header
    # The failing section is marked as incomplete in the file itself...
    assert "incomplete" in contents
    # ...and the file still holds the sections that did succeed.
    assert STUB_HOST in contents
    # A failing probe's output is not silently passed off as its section.
    assert FAILING_STUB_MARKER not in contents


@pytest.mark.parametrize("name, probe", FAILING_PROBES)
def test_the_partial_inventory_is_still_written_at_mode_0600(
    tmp_path: Path, name: str, probe: str
) -> None:
    result, target = run_with_failing_stub(tmp_path, name)

    assert result.returncode != 0
    assert target.is_file()
    assert_owner_only_mode(target)


@pytest.mark.parametrize("name, probe", FAILING_PROBES)
def test_no_temporary_sidecar_survives_a_failing_probe(
    tmp_path: Path, name: str, probe: str
) -> None:
    """The failure bookkeeping file is removed however the script exits."""

    _result, target = run_with_failing_stub(tmp_path, name)

    leftovers = sorted(
        p.name
        for p in target.parent.glob(f"{target.name}.*")
    )
    assert leftovers == [], leftovers


def test_several_failing_probes_are_all_named_once(tmp_path: Path) -> None:
    stub_dir = write_stub_path(tmp_path)
    for name in ("tailscale", "docker", "nginx"):
        write_failing_stub(stub_dir, name, FAILING_STUB_OUTPUT, 7)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(stub_dir), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )
    receipt = result.stdout + result.stderr

    assert result.returncode != 0
    for probe in ("tailscale serve status", "docker ps", "nginx -T"):
        assert probe in receipt
    # nginx is probed twice; it is named once, not once per section.
    assert receipt.count("nginx -T") == 1
    assert FAILING_STUB_MARKER not in receipt


def test_a_failing_probe_does_not_prevent_the_fallback_socket_probe(
    tmp_path: Path,
) -> None:
    """``netstat`` is only reached when ``ss`` is absent; failing ss still fails."""

    stub_dir = write_stub_path(tmp_path)
    (stub_dir / "ss").unlink()
    write_failing_stub(stub_dir, "netstat", FAILING_STUB_OUTPUT, 3)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(stub_dir), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )
    receipt = result.stdout + result.stderr

    assert result.returncode != 0
    assert "netstat -tlnp" in receipt
    assert FAILING_STUB_MARKER not in receipt
    assert_owner_only_mode(target)


def test_all_probes_succeeding_still_prints_the_success_receipt(tmp_path: Path) -> None:
    """The failure path must not fire when nothing failed."""

    target = tmp_path / "inventory.txt"

    result = run_script(tmp_path, "--output", str(target))

    assert result.returncode == 0, result.stderr
    assert "Wrote inventory" in result.stdout
    assert "PARTIAL" not in result.stdout + result.stderr


def test_missing_tools_do_not_count_as_failures(tmp_path: Path) -> None:
    """A tool that is simply absent is a clean skip, not a failed probe."""

    bare = write_bare_path(tmp_path)
    target = tmp_path / "inventory.txt"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--output", str(target)],
        capture_output=True,
        text=True,
        env={"PATH": str(bare), "HOME": str(tmp_path / "home"), "SHELL": "/bin/sh"},
        cwd=tmp_path,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote inventory" in result.stdout
    assert "PARTIAL" not in result.stdout + result.stderr
    assert "incomplete" not in target.read_text(encoding="utf-8")


def test_the_script_never_hides_a_probe_failure_with_bare_or_true() -> None:
    """``|| true`` on a probe is exactly the pattern that caused this bug."""

    source = SCRIPT.read_text(encoding="utf-8")

    assert "|| true" not in source


def test_tests_reference_only_reserved_example_data() -> None:
    """This suite must not carry a real hostname of anyone's network."""

    source = Path(__file__).read_text(encoding="utf-8")

    for line in source.splitlines():
        if "://" not in line or line.lstrip().startswith("#"):
            continue
        for token in line.split("://")[1:]:
            host = token.split("/")[0].split("'")[0].split('"')[0]
            host = host.split("@")[-1].split(":")[0]
            if not host or "{" in host:
                continue
            assert host.endswith(".invalid") or host in {
                "127.0.0.1",
                "0.0.0.0",
            }, host
