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

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Pin the test toolchain into the shared gem path at build time.
RUN gem install --no-document minitest:5.25.4 rake:13.2.1 \
    && ruby -e "require 'minitest/autorun'; puts Minitest::VERSION"

WORKDIR /workspace
