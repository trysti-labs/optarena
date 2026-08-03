# optarena-tester-jvm: sandbox for Java/Spring Boot cases.
#
# A scratch Spring Boot project is built once here (network available at
# build time) purely to warm the shared ~/.m2 cache - every dependency and
# plugin a case's own pom.xml needs (same spring-boot-starter-parent 3.5.16)
# is then already local, so `mvn -o package`/`mvn -o test` works with
# --network none at check_command time.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM maven:3-eclipse-temurin-26@sha256:d5617b9a6307e1b51dc7c55edf09bacb66f1c91fb861949c34a3a0d4e16bd241

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /warmup
COPY scratch-pom.xml pom.xml
RUN mkdir -p src/main/java/com/optarena src/test/java/com/optarena
COPY App.java src/main/java/com/optarena/App.java
COPY AppTest.java src/test/java/com/optarena/AppTest.java
RUN mvn -B package && rm -rf /warmup

# Kotlin warmup: caches kotlin-maven-plugin, kotlin-stdlib/reflect,
# jackson-module-kotlin and the allopen compiler plugin (versions managed by
# the same spring-boot parent) so Kotlin-track cases build/test fully offline.
WORKDIR /warmup-kotlin
COPY kotlin-pom.xml pom.xml
RUN mkdir -p src/main/kotlin/com/optarena src/test/kotlin/com/optarena
COPY KotlinApp.kt src/main/kotlin/com/optarena/KotlinApp.kt
COPY KotlinAppTest.kt src/test/kotlin/com/optarena/KotlinAppTest.kt
RUN mvn -B package && rm -rf /warmup-kotlin

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

# Maven's warmed repository lives under root's HOME (0700), invisible to a
# non-root uid - relocate it to a shared path and point every invocation
# there, so `mvn -o` resolves offline as either user. The symlink keeps
# the root default (and any case referencing ~/.m2) working unchanged.
RUN mv /root/.m2 /opt/m2 \
 && chmod -R a+rX /opt/m2 \
 && chmod -R a+rwX /opt/m2/repository \
 && ln -s /opt/m2 /root/.m2
ENV MAVEN_OPTS="-Dmaven.repo.local=/opt/m2/repository"

WORKDIR /workspace
