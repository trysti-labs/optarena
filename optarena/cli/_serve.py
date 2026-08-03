"""`optarena serve`: a results-dashboard HTTP server scoped to exactly
dashboard/ + the results directory - never the repository root."""

from __future__ import annotations

import http.server
import sys

from .. import store
from ._constants import REPO_ROOT


class _ScopedDashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves ONLY dashboard/ (static assets) and results/ (JSON run data) -
    never the repository root. C-04: the previous handler used
    `directory=REPO_ROOT`, which exposed source, scenarios, .git, and every
    other file in the repo to any local process/user that could reach the
    port, not just the two directories the dashboard actually needs
    (dashboard/index.html fetches "../results/index.json" and
    "../results/<run>.json" - i.e. `/results/*` - relative to `/dashboard/`).
    """

    #: url prefix -> directory on disk it's allowed to serve from. Set by
    #: cmd_serve right before the server starts, so a --results-dir override
    #: (applied earlier in main()) is reflected - REPO_ROOT/"results" here
    #: would be stale the moment store.set_results_dir() is called.
    _ROOTS = {"dashboard": REPO_ROOT / "dashboard", "results": REPO_ROOT / "results"}

    def translate_path(self, path: str) -> str:
        # Strip query/fragment the same way the base implementation does.
        path = path.split("?", 1)[0].split("#", 1)[0]
        parts = [p for p in path.split("/") if p not in ("", ".")]
        if not parts or parts[0] not in self._ROOTS:
            return ""  # signal "not servable" - do_GET below turns this into a 404
        base = self._ROOTS[parts[0]].resolve()
        candidate = (base / "/".join(parts[1:])).resolve()
        if candidate != base and base not in candidate.parents:
            return ""  # containment check failed - traversal attempt
        return str(candidate)

    def do_GET(self) -> None:
        if self.path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/dashboard/")
            self.end_headers()
            return
        if not self.translate_path(self.path):
            self.send_error(404, "Not Found")
            return
        super().do_GET()

    def list_directory(self, path):  # noqa: ANN001 - matches base signature
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self) -> None:
        # Belt-and-suspenders against embedding/sniffing from other origins;
        # this is a local single-user server, but it's trivial to add.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()


def cmd_serve(args) -> int:
    # Deferred, not the module-level REPO_ROOT: looked up through the `cli`
    # package facade at call time so `mock.patch.object(cli, "REPO_ROOT",
    # ...)` (the pre-split, still-documented patch point - REPO_ROOT lived
    # directly in cli.py before P3-01) is actually honored. A static import
    # here would capture cli/__init__.py's REPO_ROOT value ONCE at module
    # load and never see a later patch on the facade attribute, silently
    # falling through to the real dashboard/ and starting a real server
    # that blocks forever - exactly what broke a "missing dashboard" test.
    from . import REPO_ROOT
    # Reflect any --results-dir/OPTARENA_RESULTS_DIR override (applied in
    # main() before this runs) rather than the REPO_ROOT default baked in
    # at class-definition time.
    dashboard_dir = REPO_ROOT / "dashboard"
    _ScopedDashboardHandler._ROOTS = {"dashboard": dashboard_dir, "results": store.RESULTS_DIR}
    # A-26: the dashboard is not packaged into a wheel (see README's
    # source-checkout note) - say so plainly instead of serving 404s.
    if not (dashboard_dir / "index.html").is_file():
        print(f"error: no dashboard at {dashboard_dir} - `optarena serve` needs the "
              f"source checkout (dashboard/ is not bundled into the installed package)",
              file=sys.stderr)
        return 2
    host = getattr(args, "host", "127.0.0.1")
    if host != "127.0.0.1":
        # Loud, because the server has no authentication of any kind and the
        # results directory can contain prompts, generated code, and (for
        # records predating redaction) backend keys.
        print(f"WARNING: binding {host}, not localhost - the dashboard has no "
              f"authentication and exposes every saved run to that network.",
              file=sys.stderr)
    print(f"OptArena dashboard: http://{host}:{args.port}/dashboard/  (Ctrl+C to stop)")
    print(f"  serving only dashboard/ and {store.RESULTS_DIR} - not the repository root")
    try:
        server = http.server.ThreadingHTTPServer((host, args.port), _ScopedDashboardHandler)
    except OSError as exc:
        # A-26: an in-use port used to surface as a raw traceback out of
        # ThreadingHTTPServer's constructor.
        print(f"error: cannot bind {host}:{args.port} - {exc}"
              f"\n  (another optarena serve already running? try --port {args.port + 1})",
              file=sys.stderr)
        return 2
    with server as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
