from __future__ import annotations

import pytest

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.providers import ProviderProfile, _is_local_hostname


CGNAT_ADDRESS = "100.64.0.10"


def test_cgnat_overlay_is_not_trusted_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", raising=False)

    assert _is_local_hostname(CGNAT_ADDRESS) is False


def test_cgnat_overlay_is_trusted_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", "1")

    assert _is_local_hostname(CGNAT_ADDRESS) is True


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "TRUE", " 1 "])
def test_cgnat_overlay_opt_in_requires_exact_value_one(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", value)

    assert _is_local_hostname(CGNAT_ADDRESS) is False


def test_http_endpoint_on_cgnat_is_rejected_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", raising=False)

    with pytest.raises(ConfigError, match="HTTPS"):
        ProviderProfile(
            endpoint=f"http://{CGNAT_ADDRESS}:8009/v1/chat/completions",
            model="m",
        )


def test_http_endpoint_on_cgnat_is_local_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", "1")

    profile = ProviderProfile(
        endpoint=f"http://{CGNAT_ADDRESS}:8009/v1/chat/completions",
        model="m",
    )

    assert profile.data_location == "local"


def test_loopback_and_private_hosts_remain_trusted_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY", raising=False)

    assert _is_local_hostname("127.0.0.1") is True
    assert _is_local_hostname("192.168.1.5") is True
    assert _is_local_hostname("localhost") is True
