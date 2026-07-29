"""Shared fail-closed policy for GUI workspace previews."""

from __future__ import annotations

from pathlib import Path


def sensitive_preview_path(path: Path, root: Path) -> bool:
    """Return true when a workspace path can plausibly contain credentials."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    parts = {part.lower() for part in relative.parts}
    name = path.name.lower()
    dotenv = name == ".env" or name == ".envrc" or name.startswith((".env.", ".env-"))
    protected_names = {
        ".dockercfg",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".vault-token",
        "_netrc",
        "application_default_credentials.json",
        "auth_token.json",
        "client_secret.json",
        "credentials",
        "credentials.json",
        "google-credentials.json",
        "id_ed25519",
        "id_rsa",
        "oauth_token.json",
        "secrets.env",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
        "token.json",
        "tokens.json",
    }
    json_secret_variant = (
        name.startswith("client_secret")
        or name.startswith("oauth_token")
        or name.startswith("auth_token")
    ) and name.endswith(".json")
    return (
        dotenv
        or bool(parts & {".aws", ".azure", ".docker", ".git", ".gnupg", ".kube", ".ssh"})
        or json_secret_variant
        or name in protected_names
        or path.suffix.lower() in {".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
    )
