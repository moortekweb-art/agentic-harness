#!/bin/sh
# Read-only enumeration of the services running on the deployment host.
#
# Run this ON the deployment machine (as a user allowed to query the tools
# below) to capture the live topology you then transcribe into your portal
# configuration file. Nothing here mutates state and no hostnames are baked
# in; every section is skipped cleanly when its tool is not installed.
#
#   sh scripts/discover_node1_services.sh                 # -> owner config dir
#   sh scripts/discover_node1_services.sh --output PATH   # -> explicit path
#
# The output ALWAYS goes to a file, never to stdout, because it contains your
# real private hostnames and ports. The file is created with mode 0600 and
# written atomically, so it is never briefly world-readable and never a
# half-written file. There is no shell-redirection recipe here on purpose:
# "> something.txt" in the repository directory is how a topology inventory
# ends up staged by accident.
#
# The default destination is
#   ${XDG_STATE_HOME:-$HOME/.local/state}/agentic-harness/services-inventory.txt
# which sits beside — never inside — the installed package and never inside a
# source checkout.
#
# Query values that look like credentials (token, access_token, sig, signature,
# password, secret, credential, api_key, api-key, x-api-key, key, auth) are
# redacted from any URL this script captures. That is a safety net, not a
# licence to put secrets in service URLs: the portal refuses such URLs outright.
#
# A probe that is installed but fails is never reported as success: the script
# records the failing probe's name, keeps collecting the remaining sections,
# and exits nonzero with a partial receipt naming only the probes that failed.
# A probe whose tool is simply absent is a clean skip and does not fail the run.

set -eu

usage() {
    cat <<'USAGE'
Usage: discover_node1_services.sh [--output PATH]

  --output PATH   Write the inventory to PATH (created 0600).
  -h, --help      Show this message.

With no --output, writes to
${XDG_STATE_HOME:-$HOME/.local/state}/agentic-harness/services-inventory.txt
USAGE
}

output_path=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            [ "$#" -ge 2 ] || { echo "--output requires a path" >&2; exit 2; }
            output_path="$2"
            shift 2
            ;;
        --output=*)
            output_path="${1#--output=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$output_path" ]; then
    state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
    output_path="$state_home/agentic-harness/services-inventory.txt"
fi

output_dir=$(dirname -- "$output_path")
# Create the parent with restrictive permissions from the start rather than
# creating it world-readable and tightening it afterwards.
if [ ! -d "$output_dir" ]; then
    (umask 077 && mkdir -p -- "$output_dir")
fi

# Redact credential-shaped query values in anything that looks like a URL, so
# a proxy config or container command line cannot smuggle a secret into the
# inventory. Key names are kept; only the values are replaced.
redact_urls() {
    sed -E 's/([?&#][Aa][Cc][Cc][Ee][Ss][Ss]_?[Tt][Oo][Kk][Ee][Nn]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Tt][Oo][Kk][Ee][Nn]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Ss][Ii][Gg]([Nn][Aa][Tt][Uu][Rr][Ee])?=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Ss][Ee][Cc][Rr][Ee][Tt]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#]([Xx][-_])?[Aa][Pp][Ii][-_]?[Kk][Ee][Yy]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Kk][Ee][Yy]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s/([?&#][Aa][Uu][Tt][Hh]=)[^&[:space:]"'"'"']*/\1REDACTED/g;
            s#(://)[^/[:space:]@"'"'"']*:[^/[:space:]@"'"'"']*@#\1REDACTED@#g'
}

section() {
    printf '\n===== %s =====\n' "$1"
}

# Probe failures are recorded in a sidecar file rather than a variable: the
# collector runs on the left side of a pipeline, i.e. in a subshell, so nothing
# it assigns would survive. Only the probe's own fixed name is ever written —
# never its output, which is exactly the material that may carry secrets.
note_probe_failure() {
    printf '%s\n' "$1" >> "$failure_log"
}

# Run one probe, capturing its output so the recorded exit status is the
# probe's own and never a downstream grep/head. On failure the section still
# gets a marker line naming the probe, and collection continues.
#
#   run_probe <probe-name> <command...>
#
# The captured output is emitted verbatim on success; callers that need to
# filter it do so via run_probe_filtered below.
run_probe() {
    probe_name="$1"
    shift
    if probe_output=$("$@" 2>&1); then
        printf '%s\n' "$probe_output"
    else
        probe_status=$?
        note_probe_failure "$probe_name"
        printf '%s failed (exit %s); section incomplete\n' "$probe_name" "$probe_status"
    fi
    unset probe_output
}

collect() {
    section "tailscale serve status"
    if command -v tailscale >/dev/null 2>&1; then
        run_probe "tailscale serve status" tailscale serve status
    else
        echo "tailscale not installed; skipping"
    fi

    section "tailscale status --json (head)"
    if command -v tailscale >/dev/null 2>&1; then
        if probe_output=$(tailscale status --json 2>&1); then
            printf '%s\n' "$probe_output" | head -n 40
        else
            note_probe_failure "tailscale status --json"
            echo "tailscale status --json failed; section incomplete"
        fi
        unset probe_output
    else
        echo "tailscale not installed; skipping"
    fi

    section "docker containers"
    if command -v docker >/dev/null 2>&1; then
        run_probe "docker ps" \
            docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
    else
        echo "docker not installed; skipping"
    fi

    section "listening TCP sockets"
    if command -v ss >/dev/null 2>&1; then
        run_probe "ss -tlnp" ss -tlnp
    elif command -v netstat >/dev/null 2>&1; then
        run_probe "netstat -tlnp" netstat -tlnp
    else
        echo "neither ss nor netstat installed; skipping"
    fi

    section "nginx virtual hosts and proxied locations"
    if command -v nginx >/dev/null 2>&1; then
        # Capture first so the exit status checked is nginx's, not grep's: a
        # dumped config that fails (usually "needs root") must not read as an
        # empty but successful section.
        if nginx_config=$(nginx -T 2>/dev/null); then
            printf '%s\n' "$nginx_config" \
                | grep -E 'server_name|listen|location|proxy_pass' \
                || echo "no matching nginx directives"
        else
            note_probe_failure "nginx -T"
            echo "nginx -T failed (may need root); section incomplete"
        fi
        unset nginx_config
    else
        echo "nginx not installed; skipping"
    fi

    section "nginx server-status / stub_status endpoints"
    if command -v nginx >/dev/null 2>&1; then
        if nginx_status=$(nginx -T 2>/dev/null); then
            printf '%s\n' "$nginx_status" \
                | grep -E 'stub_status|server-status|server_status' \
                || echo "no status endpoints configured"
        else
            note_probe_failure "nginx -T"
            echo "nginx -T failed (may need root); section incomplete"
        fi
        unset nginx_status
    else
        echo "nginx not installed; skipping"
    fi

    section "apache server-status (if apache is present)"
    if command -v apachectl >/dev/null 2>&1; then
        if apache_config=$(apachectl -S 2>/dev/null); then
            printf '%s\n' "$apache_config"
        else
            note_probe_failure "apachectl -S"
            echo "apachectl -S failed (may need root); section incomplete"
        fi
        unset apache_config
    else
        echo "apachectl not installed; skipping"
    fi

    section "running AI/serving-related systemd services"
    if command -v systemctl >/dev/null 2>&1; then
        if probe_output=$(systemctl list-units --type=service --state=running 2>&1); then
            printf '%s\n' "$probe_output" \
                | grep -iE 'jupyter|ray|vllm|ollama|studio|hub' \
                || echo "no matching running services"
        else
            note_probe_failure "systemctl list-units"
            echo "systemctl list-units failed; section incomplete"
        fi
        unset probe_output
    else
        echo "systemctl not available; skipping"
    fi
}

# Write atomically at 0600: build the whole inventory in a sibling temporary
# file that only the owner can read, then rename it into place.
umask 077
tmp_output="$output_path.tmp.$$"
# Sidecar holding one probe name per failed probe. It is temporary, owner-only,
# carries no command output, and is removed however this script exits.
failure_log="$output_path.failures.$$"
trap 'rm -f -- "$tmp_output" "$failure_log"' EXIT INT TERM
: > "$tmp_output"
chmod 600 "$tmp_output"
: > "$failure_log"
chmod 600 "$failure_log"

# ``collect`` must not abort the run on the first failing probe: each probe
# handles its own nonzero exit, records its name, and the later sections are
# still collected. ``set -e`` is disabled for the collection pipeline so no
# unchecked command can end it silently, and because the pipeline's status is
# sed's anyway — which is why a failing probe used to read as a clean success.
set +e
collect | redact_urls >> "$tmp_output"
set -e

mv -- "$tmp_output" "$output_path"
chmod 600 "$output_path"

if [ -s "$failure_log" ]; then
    # Partial inventory: keep the useful part, but never claim success. Only
    # probe names cross into the receipt — no topology, no captured output.
    failed_probes=$(sort -u -- "$failure_log" | tr '\n' ';' | sed 's/;$//; s/;/; /g')
    rm -f -- "$failure_log"
    trap - EXIT INT TERM
    printf 'PARTIAL inventory (mode 0600) written to: %s\n' "$output_path" >&2
    printf 'It is INCOMPLETE: these probes failed: %s\n' "$failed_probes" >&2
    printf 'Re-run (possibly as root) before trusting the contents.\n' >&2
    exit 1
fi

rm -f -- "$failure_log"
trap - EXIT INT TERM

printf 'Wrote inventory (mode 0600) to: %s\n' "$output_path"
printf 'It contains your real private hostnames and ports. Do not commit it,\n'
printf 'and do not copy it into the installed package directory.\n'
