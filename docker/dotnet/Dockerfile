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

WORKDIR /workspace
