# optarena-tester-dotnet: sandbox for C#/ASP.NET Core minimal API cases.
#
# A scratch minimal-API project plus an xUnit + WebApplicationFactory test
# project are restored/built/tested once here (network available at build
# time) to warm the shared NuGet global-packages folder. offline.nuget.config
# is then installed as the machine-wide NuGet.Config with that folder as the
# ONLY source, so `dotnet restore`/`test` on a case's own project (pinned to
# the same package versions) succeeds fully offline at check_command time
# (--network none).
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:89ce6291bde9acdf59594e79fb8277c6d84c46e4b1f5bf126a4f18766e4bd597

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /warmup
COPY warmup.csproj src/warmup.csproj
COPY Program.cs src/Program.cs
COPY warmup.Tests.csproj tests/warmup.Tests.csproj
COPY HealthTests.cs tests/HealthTests.cs
RUN dotnet test tests/warmup.Tests.csproj \
    && rm -rf /warmup

COPY offline.nuget.config /root/.nuget/NuGet/NuGet.Config

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

# The offline NuGet config and package cache sit under root's HOME (0700).
# Publish them at a shared path so an unprivileged uid can restore offline,
# and give the CLI a writable home on the tmpfs.
RUN cp -r /root/.nuget /opt/nuget \
 && chmod -R a+rX /opt/nuget \
 && (chmod -R a+rwX /opt/nuget/packages 2>/dev/null || true)
ENV NUGET_PACKAGES=/opt/nuget/packages
ENV DOTNET_CLI_HOME=/tmp
ENV DOTNET_NOLOGO=1

WORKDIR /workspace
