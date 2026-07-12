// The node globals src/ touches, so `"types": []` (no @types/node in the
// sandbox) still compiles under strict.
declare const process: {
  env: Record<string, string | undefined>;
};

declare const console: {
  log(...args: unknown[]): void;
  error(...args: unknown[]): void;
};
