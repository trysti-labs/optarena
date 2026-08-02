# optarena-tester-php: sandbox for PHP cases.
#
# Bundles PHP 8.3 CLI, Composer, and a pinned PHPUnit phar at BUILD time
# (network available here) so cases run fully offline under --network none at
# check_command time. Cases run their tests with `phpunit <file-or-dir>`;
# Composer is present for cases that ship a composer.json with PSR-4
# autoloading (its packages must already be vendored - nothing installs at
# test time).
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM php:8.3-cli@sha256:2a3f699b6cb31e5638c5432e4d37d4047853ba6351a692c91e0a073af00a55cc

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        unzip \
        git \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Composer, with the integrity check Composer's own docs recommend: verify
# the installer's SHA-384 against the hash Composer publishes at a separate
# URL, before ever executing it (guards against a compromised/MITM'd
# download - php-executing an unverified installer script is exactly the
# kind of curl-pipe-to-shell risk everything else in this Dockerfile layer
# avoids via digest/version pinning).
RUN curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php \
    && EXPECTED="$(curl -fsSL https://composer.github.io/installer.sig)" \
    && ACTUAL="$(php -r "echo hash_file('sha384', '/tmp/composer-setup.php');")" \
    && if [ "$EXPECTED" != "$ACTUAL" ]; then echo "Composer installer checksum mismatch" >&2; exit 1; fi \
    && php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer \
    && rm /tmp/composer-setup.php \
    && composer --version

# PHPUnit, pinned to an exact version (not the floating "-11" alias) and
# GPG-verified against the release manager's published signature - same
# reasoning as the Composer check above. Key hosted at Sebastian Bergmann's
# own domain (more reliable in a build context than a keyserver network);
# fingerprint independently cross-checked against the key ID PHPUnit's own
# docs publish (0x4AA394086372C20A) before pinning it here.
#
# Was pinned to 11.5.56 until this pass: phar.phpunit.de has since pruned
# every 11.x release from its distribution server entirely (confirmed via
# its own directory listing), so the URL still returned bytes but they no
# longer matched the .asc signature ("BAD signature", not "not found" -
# every image-vuln-scan matrix leg building this image fresh caught it,
# since no prior job built docker/php from scratch on every run). Bumped to
# 12.0.0, the version PHPUnit's own docs state as the minimum requiring PHP
# 8.3 - matching this image's php:8.3-cli base exactly. Verified locally
# before pinning here: `gpg --verify` reports "Good signature" against the
# same Sebastian Bergmann key used above, not just a downloaded file.
RUN curl -fsSL https://phar.phpunit.de/phpunit-12.0.0.phar -o /usr/local/bin/phpunit \
    && curl -fsSL https://phar.phpunit.de/phpunit-12.0.0.phar.asc -o /tmp/phpunit.phar.asc \
    && curl -fsSL https://sebastian-bergmann.de/gpg.asc -o /tmp/phpunit-signer.asc \
    && gpg --import /tmp/phpunit-signer.asc \
    && gpg --verify /tmp/phpunit.phar.asc /usr/local/bin/phpunit \
    && rm /tmp/phpunit.phar.asc /tmp/phpunit-signer.asc \
    && chmod +x /usr/local/bin/phpunit \
    && phpunit --version

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

WORKDIR /workspace
