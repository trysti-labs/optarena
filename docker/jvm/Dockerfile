# optarena-tester-jvm: sandbox for Java/Spring Boot cases.
#
# A scratch Spring Boot project is built once here (network available at
# build time) purely to warm the shared ~/.m2 cache - every dependency and
# plugin a case's own pom.xml needs (same spring-boot-starter-parent 3.3.4)
# is then already local, so `mvn -o package`/`mvn -o test` works with
# --network none at check_command time.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM maven:3.9-eclipse-temurin-17@sha256:1ed5d1f54416b706707b4f3238f63a20bb06aab27c6d240090a2bb9ad895ed45

RUN apt-get update && apt-get install -y --no-install-recommends \
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

WORKDIR /workspace
