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
    && rm -rf /var/lib/apt/lists/*

# Composer (for PSR-4 autoloading in cases that want it).
RUN curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php \
    && php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer \
    && rm /tmp/composer-setup.php \
    && composer --version

# PHPUnit 11 as a standalone phar - no per-case composer install needed.
RUN curl -fsSL https://phar.phpunit.de/phpunit-11.phar -o /usr/local/bin/phpunit \
    && chmod +x /usr/local/bin/phpunit \
    && phpunit --version

WORKDIR /workspace
