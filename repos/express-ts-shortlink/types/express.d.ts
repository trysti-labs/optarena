// Minimal ambient typings for the express API surface this app uses.
//
// The OptArena node sandbox provides the express PACKAGE globally (resolved
// via NODE_PATH at runtime), but tsc does not consult NODE_PATH and there is
// no per-project node_modules - so the compile-time view of express lives
// here. The function + namespace merge mirrors @types/express: values come
// from `express` / `express.Router()`, and types are reachable both as
// `express.Response` and via named import. Extend these typings if you use
// more of the express API.
declare module "express" {
  namespace express {
    interface Request {
      params: Record<string, string>;
      query: Record<string, string | string[] | undefined>;
      body: any;
      path: string;
      method: string;
    }

    interface Response {
      status(code: number): Response;
      json(body: unknown): Response;
      send(body?: unknown): Response;
      redirect(status: number, url: string): void;
      setHeader(name: string, value: string): void;
    }

    type Handler = (req: Request, res: Response) => void;
    type Middleware = (req: Request, res: Response, next: () => void) => void;

    interface Router {
      get(path: string, handler: Handler): void;
      post(path: string, handler: Handler): void;
      delete(path: string, handler: Handler): void;
      use(handler: Middleware): void;
    }

    interface Server {
      address(): { port: number } | string | null;
      close(callback?: () => void): void;
    }

    interface Application extends Router {
      use(mountPathOrHandler: unknown, router?: unknown): void;
      listen(port: number, callback?: () => void): Server;
    }

    function Router(): Router;
    function json(): Middleware;
  }

  function express(): express.Application;

  export = express;
}
