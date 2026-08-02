# docker-multiservice

A small multi-service Docker Compose setup: `api` (Flask placeholder),
`worker` (background job placeholder), and `nginx` (reverse proxy in front
of `api`), each with its own Dockerfile under its own directory, wired
together by `docker-compose.yml`.

Like the rest of this corpus's Dockerfile cases, there's no real `docker
build` here - cases are checked with `python3 check_X.py` scripts that do
static, regex-based structural assertions against the Dockerfile/compose
text, including cross-file consistency (e.g. a port `EXPOSE`d by a
Dockerfile must match what `docker-compose.yml` actually maps to it).
