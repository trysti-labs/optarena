# Returns 0 if all named environment variables are set and non-empty,
# 1 otherwise (logging each missing one).
validate_env() {
    local missing=0
    local var_name
    for var_name in "$@"; do
        if [ -z "${!var_name:-}" ]; then
            log_error "missing required environment variable: $var_name"
            missing=1
        fi
    done
    return "$missing"
}

# Returns 0 if $1 matches a semantic version X.Y.Z, 1 otherwise.
validate_version() {
    if [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        return 0
    fi
    log_error "invalid version format: $1 (expected X.Y.Z)"
    return 1
}
