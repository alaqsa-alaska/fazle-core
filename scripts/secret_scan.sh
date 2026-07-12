#!/usr/bin/env bash
# Lightweight secret-pattern scanner — no external dependency (gitleaks/
# trufflehog are not installed on this host; see docs/DEPLOYMENT_RESTART_GATE.md
# check 2 for why this pattern-list approach is the deliberate MVP choice).
#
# Scans a given file list (or all git-tracked files if none given) for
# common leaked-credential shapes. Exits non-zero with match lines printed
# if anything hits; silent + exit 0 otherwise.
#
# Usage:
#   scripts/secret_scan.sh                  # scan all git-tracked files
#   scripts/secret_scan.sh file1 file2 ...  # scan a specific file list (e.g. staged files)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Patterns tuned to this repo's known-leaked-secret shapes (INTERNAL_API_KEY,
# HR-officer keys/passwords found 2026-07-08) plus generic high-entropy
# key/password assignments. Deliberately conservative — false negatives are
# safer to iterate on than a noisy scanner nobody trusts.
PATTERNS=(
    '(API_KEY|APIKEY|SECRET|TOKEN)[A-Za-z0-9_]*[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_\-]{20,}'
    '(PASSWORD|PASSWD)[A-Za-z0-9_]*[[:space:]]*[:=][[:space:]]*[^"'\'' \t]{8,}'
    'postgresql://[^:]+:[^@]{6,}@'
)

# Known-safe allowlist: lines deliberately containing example/placeholder
# values, or the template files where __PLACEHOLDER__-style values live.
ALLOWLIST_FILES=(
    '.env.example'
    '.env.bak.*'
    'CHAOS_TEST_PLAN.md'
    'docs/DEPLOYMENT_RESTART_GATE.md'
    'scripts/secret_scan.sh'
)

# Documented-safe literal substrings (per docs/DEPLOYMENT_RESTART_GATE.md
# check 2: "a deliberately-shown example key in a test fixture"). Reviewed
# 2026-07-12: `postgres:postgres@` is the official postgres Docker image's
# own well-known default credential, used only by ephemeral CI/local test
# containers (never a real DB); `test-internal-key-fixture-only` is a
# self-documenting dummy value. Add new entries here deliberately, not to
# silence a real finding.
SAFE_VALUE_SUBSTRINGS=(
    'postgres:postgres@'
    'test-internal-key-fixture-only'
)

is_safe_value() {
    local line="$1"
    for safe in "${SAFE_VALUE_SUBSTRINGS[@]}"; do
        [[ "$line" == *"$safe"* ]] && return 0
    done
    return 1
}

is_allowlisted_file() {
    local f="$1"
    for pat in "${ALLOWLIST_FILES[@]}"; do
        # shellcheck disable=SC2053
        [[ "$f" == $pat ]] && return 0
    done
    return 1
}

if [[ $# -gt 0 ]]; then
    FILES=("$@")
else
    mapfile -t FILES < <(git ls-files)
fi

# A match whose value-looking part is actually a variable reference
# ({name}, $(NAME), ${NAME}, f-string braces) is not a literal secret —
# drop those before counting, they're the dominant false-positive source.
is_var_reference() {
    grep -qE '[:=][[:space:]]*["'\'']?(\{|\$\(|\$\{)' <<<"$1"
}

MATCHES=0
for f in "${FILES[@]}"; do
    [[ -f "$f" ]] || continue
    is_allowlisted_file "$f" && continue
    for pat in "${PATTERNS[@]}"; do
        if hits=$(grep -nEI "$pat" -- "$f" 2>/dev/null); then
            while IFS= read -r line; do
                is_var_reference "$line" && continue
                is_safe_value "$line" && continue
                echo "  $f:$line"
                MATCHES=$((MATCHES + 1))
            done <<<"$hits"
        fi
    done
done

if [[ "$MATCHES" -gt 0 ]]; then
    echo "secret_scan: $MATCHES potential match(es) found" >&2
    exit 1
fi
exit 0
