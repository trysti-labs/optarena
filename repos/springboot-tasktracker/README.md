# springboot-tasktracker

Shared L3 (repo-scale) starter app for OptArena: the same domain as
`fastapi-tasktracker` (users own projects, projects contain tasks), on
Spring Boot 3.3 / Java 17, tested with MockMvc.

Repositories are plain in-memory `ConcurrentHashMap`s (`UserRepository`,
`ProjectRepository`, `TaskRepository`), not Spring Data JPA + a real
database: `optarena-tester-jvm`'s offline Maven cache is only warmed for
`spring-boot-starter-web` and `spring-boot-starter-test` (see
`docker/jvm/scratch-pom.xml`), and `check_command` runs with `--network
none`, so `spring-boot-starter-data-jpa` / `com.h2database` would fail to
resolve. The in-memory layer keeps every case in this fixture buildable
fully offline without changing the sandbox image.

```
src/main/java/com/optarena/tasktracker/
  TasktrackerApplication.java   App entry point
  model/                        User, Project, Task, TaskStatus (POJOs)
  repository/                   In-memory CRUD + pagination per entity
  dto/                          Request/response records
  service/                      Business logic (pagination, filtering, progress)
  controller/                   REST endpoints per entity
  exception/                    NotFoundException -> 404 via @RestControllerAdvice
src/test/java/com/optarena/tasktracker/   MockMvc suite, one class per controller
```

Run the suite from the repo root: `mvn -o -q test`.

This repo is copied verbatim into a case's workspace by OptArena's
`setup_repo` schema field (see `optarena/cases.py:copy_setup_repo`) - it is
not embedded in any case JSON. Individual L3 cases layer a small
`setup_files` diff on top and a hidden test class dropped into
`src/test/java/com/optarena/tasktracker/` that `check_command` runs
alongside this suite.
