# optarena-tester-dotnet: sandbox for C#/ASP.NET Core minimal API cases.
#
# A scratch minimal-API project plus an xUnit + WebApplicationFactory test
# project are restored/built/tested once here (network available at build
# time) to warm the shared NuGet global-packages folder. offline.nuget.config
# is then installed as the machine-wide NuGet.Config with that folder as the
# ONLY source, so `dotnet restore`/`test` on a case's own project (pinned to
# the same package versions) succeeds fully offline at check_command time
# (--network none).
FROM mcr.microsoft.com/dotnet/sdk:8.0

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
