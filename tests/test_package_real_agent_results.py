from evaluation.package_real_agent_results import redact


def test_redact_removes_session_ids_and_local_paths() -> None:
    value = "session id: abc-123\nworkdir: /tmp/private/work\nsource /mnt/raid0/home/file\n"
    redacted = redact(value)
    assert "abc-123" not in redacted
    assert "/tmp/private" not in redacted
    assert "/mnt/raid0" not in redacted
