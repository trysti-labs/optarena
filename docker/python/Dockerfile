# optarena-tester-python: sandbox for Python/FastAPI/Pydantic/Flask/Django cases.
#
# All dependencies are installed at BUILD time (network available here) since
# check_command runs with --network none - nothing can be pip installed at
# test time. Add a package here, don't add it to a case's check_command.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages \
        fastapi \
        pydantic \
        uvicorn \
        httpx \
        requests \
        pytest \
        flask \
        django \
        sqlalchemy \
        typer \
        pyyaml

WORKDIR /workspace
