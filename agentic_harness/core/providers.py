"""Provider profiles and secret-reference handling."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from agentic_harness.core.errors import ConfigError


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ProviderTemplate:
    """Editable convenience values for a provider-neutral setup form."""

    key: str
    label: str
    description: str
    endpoint: str = ""
    model: str = ""
    api_key_env: str = ""
    entitlement_note: str = ""

    def to_public_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "endpoint": self.endpoint,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "entitlement_note": self.entitlement_note,
        }


PROVIDER_TEMPLATES: tuple[ProviderTemplate, ...] = (
    ProviderTemplate(
        key="custom",
        label="Custom OpenAI-compatible provider",
        description="Enter any compatible endpoint and model ID.",
    ),
    ProviderTemplate(
        key="zai_api",
        label="Z.ai API",
        description="Start with Z.ai's general OpenAI-compatible endpoint.",
        endpoint="https://api.z.ai/api/paas/v4/chat/completions",
        model="glm-5.1",
        api_key_env="ZAI_API_KEY",
    ),
    ProviderTemplate(
        key="zai_coding_plan",
        label="Z.ai GLM Coding Plan",
        description="Use a GLM Coding Plan entitlement with editable endpoint and model values.",
        endpoint="https://api.z.ai/api/coding/paas/v4/chat/completions",
        model="glm-5.2",
        api_key_env="ZAI_API_KEY",
        entitlement_note=(
            "Z.ai limits the Coding Plan endpoint to supported coding tools; confirm that your "
            "account and client are eligible before using this template."
        ),
    ),
)


@dataclass(frozen=True)
class ProviderProfile:
    """Non-secret configuration for one OpenAI-compatible model endpoint."""

    endpoint: str
    model: str
    api_key_env: str = ""

    def __post_init__(self) -> None:
        endpoint = self.endpoint.strip()
        model = self.model.strip()
        if not endpoint:
            raise ConfigError("model provider endpoint must not be empty")
        if not model:
            raise ConfigError("model provider model must not be empty")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("model provider endpoint must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ConfigError("model provider endpoint must not contain credentials")
        if parsed.fragment:
            raise ConfigError("model provider endpoint must not contain a URL fragment")
        if parsed.query:
            raise ConfigError("model provider endpoint must not contain a URL query")
        if parsed.scheme == "http" and not _is_local_hostname(parsed.hostname):
            raise ConfigError("public cloud model endpoints must use HTTPS")
        key_env = self.api_key_env.strip()
        if key_env and _ENV_NAME.fullmatch(key_env) is None:
            raise ConfigError("api_key_env must be a valid environment variable name")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key_env", key_env)

    @property
    def data_location(self) -> str:
        hostname = urlparse(self.endpoint).hostname or ""
        return "local" if _is_local_hostname(hostname) else "cloud"

    def to_public_dict(self) -> dict[str, str]:
        return {
            "kind": "openai_compatible",
            "endpoint": self.endpoint,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "data_location": self.data_location,
        }


def resolve_api_key(
    env_name: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve an API key without ever persisting it in project configuration."""

    name = env_name.strip()
    if not name:
        return ""
    if _ENV_NAME.fullmatch(name) is None:
        raise ConfigError("api_key_env must be a valid environment variable name")
    source = os.environ if environ is None else environ
    value = source.get(name, "").strip()
    if not value:
        raise ConfigError(f"API key environment variable {name} is not set")
    return value


def _is_local_hostname(hostname: str) -> bool:
    normalized = hostname.strip().strip("[]").lower()
    if normalized in {"localhost", "host.docker.internal"} or normalized.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)
