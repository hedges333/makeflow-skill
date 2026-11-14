# Polyglot Stack Guide: TypeScript and JavaScript

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for TypeScript and Node.js.

## 1. Detection Markers

Look for these files when scanning a TypeScript or JavaScript subproject:

- `package.json`, `tsconfig.json`, `pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`

## 2. Standard Commands

Run these commands inside the subproject directory:

- Targeted Test (TypeScript): `npm test`, `npx vitest run <path>`, or `npx jest <path>`
- Targeted Test (Node.js built-in): `node --test`
- Format: `npx prettier --check .` (write mode: `npx prettier --write .`)
- Lint: `npx eslint .`

## 3. Smoke Test Templates

If no test directory exists, create `tests/smoke.test.ts` or `tests/smoke.test.js`.

### TypeScript (`tests/smoke.test.ts`)

```typescript
import { describe, expect, it } from 'vitest';

describe('Smoke Test Harness', () => {
  it('runs cleanly', () => {
    expect(true).toBe(true);
  });
});
```

### Node.js JavaScript (`tests/smoke.test.js`)

```javascript
const test = require('node:test');
const assert = require('node:assert');

test('smoke test harness', () => {
  assert.strictEqual(true, true);
});
```

## 4. Test Database and Network Isolation

Follow three isolation rules in Node.js and TypeScript tests:

1. Make sure that `NODE_ENV=test` is set in every test command.
2. Use in-memory SQLite (`better-sqlite3` with `:memory:`) or local Docker test containers.
3. Stub external HTTP requests with `msw` (Mock Service Worker) or `nock`.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place pure domain modules in `src/lib/` or `src/domain/` and export the public package surface through `src/index.ts`.
- **Executable Entrypoints and Controllers**: Place thin CLI scripts or server bootstrap files in `src/cli/` or `bin/` that import one-way from `src/lib/`.
- **Test Suite Placement and Naming**: Place Vitest or Jest suites in `tests/*.test.ts` (or co-located `*.spec.ts`) and exclude test files from `tsconfig.build.json`.
- **Structural Anti-Patterns to Flag at Target 15**: Flag circular barrel `index.ts` imports, business logic in CLI entrypoints, or unguarded top-level side effects on module import.
