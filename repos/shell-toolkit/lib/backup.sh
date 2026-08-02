# Copies $1 to "$1.bak.$2" - the caller supplies an explicit suffix (e.g. a
# build id) rather than this function using the wall clock, so behavior is
# deterministic and testable.
backup_file() {
    local src="$1"
    local suffix="$2"
    if [ ! -f "$src" ]; then
        log_error "cannot back up missing file: $src"
        return 1
    fi
    cp "$src" "${src}.bak.${suffix}"
}
