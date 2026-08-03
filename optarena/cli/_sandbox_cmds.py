"""The `optarena sandbox` commands: build/pull/status for the
`optarena-tester*` Docker images."""

from __future__ import annotations

import sys

from ..cases import DOCKER_IMAGES, container_engine, dockerfile_for, docker_image_available


def cmd_docker(args) -> int:
    """Build (or pull) one or all of the `optarena-tester*` sandbox images."""
    import subprocess as _sp

    if args.action == "pull":
        from ..cases import docker_image_pull
        langs = list(DOCKER_IMAGES) if args.all else [args.lang or "base"]
        overall = 0
        for lang in langs:
            image = DOCKER_IMAGES[lang]
            if docker_image_available(image):
                print(f"  {image} already present")
            elif not docker_image_pull(image):
                overall = 1
        return overall

    if args.action != "build":
        print(f"unknown docker action: {args.action}", file=sys.stderr)
        return 2

    if args.all:
        langs = list(DOCKER_IMAGES)
    elif args.lang:
        if args.lang not in DOCKER_IMAGES:
            print(f"unknown --lang '{args.lang}' - choices: {', '.join(DOCKER_IMAGES)}", file=sys.stderr)
            return 2
        langs = [args.lang]
    else:
        langs = ["base"]

    overall = 0
    for lang in langs:
        image = DOCKER_IMAGES[lang]
        dockerfile = dockerfile_for(lang)
        if not dockerfile.exists():
            print(f"no Dockerfile at {dockerfile} - skipping {lang}", file=sys.stderr)
            overall = 1
            continue
        engine = container_engine()
        print(f"building {image} from {dockerfile} (via {engine}) ...")
        proc = _sp.run(
            [engine, "build", "-t", image, "-f", str(dockerfile), str(dockerfile.parent)],
        )
        if proc.returncode == 0:
            print(f"  built {image}")
        else:
            overall = proc.returncode
    return overall


def cmd_sandbox_status(args) -> int:
    """Scriptable view of what `doctor`'s container-engine section reports:
    daemon reachability and which sandbox images are built locally."""
    import subprocess as _sp

    engine = container_engine()
    try:
        running = _sp.run([engine, "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, _sp.TimeoutExpired):
        running = False
    print(f"  {engine} daemon: {'reachable' if running else 'NOT reachable'}")
    if not running:
        print("  (install/start Docker or Podman - without it check_command refuses to run; "
              "see OPTARENA_ALLOW_UNSAFE_HOST_EXEC in the docs)")
        return 1
    missing = 0
    for lang, image in DOCKER_IMAGES.items():
        built = docker_image_available(image)
        if not built:
            missing += 1
        hint = "" if built else \
            f"  <- optarena sandbox build --lang {lang}" if lang != "base" else "  <- optarena sandbox build"
        print(f"  [{'ok ' if built else 'MISS'}] {image}{hint}")
    return 0 if missing == 0 else 1
