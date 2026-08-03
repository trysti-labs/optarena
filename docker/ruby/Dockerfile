# optarena-tester-ruby: sandbox for Ruby cases.
#
# Ruby 3.3 with Minitest and Rake installed at BUILD time (network available
# here) so cases run fully offline under --network none at check_command time.
# Minitest ships in the stdlib, but we pin an explicit gem so the version is
# stable across base-image updates. Cases run their tests with
# `ruby -Itest -Ilib test/<name>_test.rb`; a test file requires the code under
# test via a relative require. No gems install at check_command time.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM ruby:3.3-slim@sha256:fb6613e39db7b37efb7976390f0fcd4da2ea018a74770ae4a01d21a4c441f637

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Pin the test toolchain into the shared gem path at build time.
RUN gem install --no-document minitest:5.25.4 rake:13.2.1 \
    && ruby -e "require 'minitest/autorun'; puts Minitest::VERSION"

# A-36: an unprivileged account so `OPTARENA_SANDBOX_USER=1000:1000` is
# usable at all. The image still DEFAULTS to root (no USER directive): the run
# root is a HOST bind mount whose ownership a container cannot change, so
# flipping the default needs per-track validation on real Linux (see the
# sandbox-nonroot CI job). What was missing before is what this adds - the
# account itself, a writable HOME, and toolchain caches an unprivileged uid
# can actually read: with none of that, the documented opt-in failed
# immediately on every image, which is why it was never a real mitigation.
#
# Adopts a pre-existing uid 1000 where the base image already has one
# (node:20 ships `node`, maven:3.9-eclipse-temurin-17 ships `ubuntu`)
# instead of failing on a duplicate. Parsed with parameter expansion
# rather than `cut` so the RUN carries no pipe (hadolint DL4006).
RUN set -eu; \
    if ! getent group 1000 >/dev/null; then groupadd -g 1000 optarena; fi; \
    if ! getent passwd 1000 >/dev/null; then \
        useradd -u 1000 -g 1000 -m -d /home/optarena -s /bin/sh optarena; \
    fi; \
    entry="$(getent passwd 1000)"; \
    without_shell="${entry%:*}"; \
    home="${without_shell##*:}"; \
    mkdir -p "$home"; \
    chmod 0777 "$home"

# Gems install into a shared path; make sure a non-root uid can read them.
RUN chmod -R a+rX "$(gem environment gemdir)" 2>/dev/null || true

WORKDIR /workspace
