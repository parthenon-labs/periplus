// Dev-only preview harness. Lets us drive the real UI against the existing,
// schema-typed test fixtures (see src/test/fixtures.ts) without a running
// backend — for local iteration and screenshot verification only. Gated
// behind import.meta.env.DEV in main.tsx and a "?mock=1" query flag, so it
// never ships in a production build.
import { setupWorker } from 'msw/browser'
import { handlers } from '../test/mocks/handlers'

export const worker = setupWorker(...handlers)
