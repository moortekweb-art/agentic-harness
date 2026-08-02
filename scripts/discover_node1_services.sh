#!/bin/sh
# Read-only enumeration of the services running on the deployment host.
#
# Run this ON the deployment machine (as a user allowed to query the tools
# below) to capture the live topology you then copy into
# agentic_harness/gui/static/services.local.json for the portal page.
# Nothing here mutates state and no hostnames are baked in; every section is
# skipped cleanly when its tool is not installed. Output goes to stdout.
#
#   sh scripts/discover_node1_services.sh > services-inventory.txt
#
# The inventory it prints contains your real private hostnames and ports.
# Keep it out of version control (services.local.json already is).

section() {
    printf '\n===== %s =====\n' "$1"
}

section "tailscale serve status"
if command -v tailscale >/dev/null 2>&1; then
    tailscale serve status 2>&1
else
    echo "tailscale not installed; skipping"
fi

section "tailscale status --json (head)"
if command -v tailscale >/dev/null 2>&1; then
    tailscale status --json 2>&1 | head -n 40
else
    echo "tailscale not installed; skipping"
fi

section "docker containers"
if command -v docker >/dev/null 2>&1; then
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' 2>&1
else
    echo "docker not installed; skipping"
fi

section "listening TCP sockets"
if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>&1
elif command -v netstat >/dev/null 2>&1; then
    netstat -tlnp 2>&1
else
    echo "neither ss nor netstat installed; skipping"
fi

section "nginx virtual hosts and proxied locations"
if command -v nginx >/dev/null 2>&1; then
    nginx -T 2>/dev/null | grep -E 'server_name|listen|location|proxy_pass'
    [ "$?" -le 1 ] || echo "nginx -T failed (may need root); skipping"
else
    echo "nginx not installed; skipping"
fi

section "running AI/serving-related systemd services"
if command -v systemctl >/dev/null 2>&1; then
    systemctl list-units --type=service --state=running 2>&1 \
        | grep -iE 'jupyter|ray|vllm|ollama|studio|hub' \
        || echo "no matching running services"
else
    echo "systemctl not available; skipping"
fi
