#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/validate.sh"
source "$SCRIPT_DIR/lib/backup.sh"

VERSION=""
while getopts "v:" opt; do
    case "$opt" in
        v) VERSION="$OPTARG" ;;
        *)
            log_error "usage: deploy.sh -v VERSION"
            exit 1
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    log_error "missing -v VERSION"
    exit 1
fi

if ! validate_version "$VERSION"; then
    exit 1
fi

log_info "deploying version $VERSION"
