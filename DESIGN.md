# Periplus web — design notes

This documents the reasoning behind the `web/` client as built, not an aspirational
spec. It exists so later changes (a new page, a new verdict colour, a new component)
have something to check themselves against instead of re-deriving conventions from
scratch. Anything described here is something the code under `web/src` actually does;
if the code and this file disagree, the code is probably right and this file is stale —
fix whichever one is wrong.

## 1. Information architecture: three stages, three routes

A run has exactly one lifecycle: submit a brief, watch it move through the pipeline,
read the result. The client mirrors that directly instead of inventing its own
navigation model:

| Route | Page | Purpose |
| --- | --- | --- |
| `/` | `TripInputPage` | Collect a `TripBriefCreate` and submit it (`POST /trips`). |
| `/runs/:runId` | `RunProgressPage` | Poll `GET /runs/:runId` while the run is `pending`/`running`. |
| `/runs/:runId/result` | `RunResultPage` | Read the finished itinerary (`GET /runs/:runId/result`). |

Routing lives in `web/src/App.tsx` (React Router `Routes`); any unmatched path redirects
to `/`. There is no separate "list of past runs" page yet, even though `GET /runs`
exists server-side — the client only ever follows one run at a time, from the id it just
created. Adding a run list is a real future page, not a variant of an existing one.

`RunProgressPage` auto-navigates to the result page the moment `summary.status ===
'succeeded'` (see the `useEffect` in that file); a failed or cancelled run stays on the
progress page and shows its terminal error inline instead of a blank result page. This
is a deliberate rule: **the result page only ever renders a run that actually has an
itinerary.** `RunResultPage` still defends itself (`isError || !result?.run ||
!itinerary`) for the case where someone lands on the URL directly (bookmark, refresh,
back button) before the run is ready — a "no itinerary yet" state with a link back to
the progress page, not a crash.

Server-owned identity: the client never invents or edits a run id, brief id, claim id,
etc. — everything with an `id` in `contracts.ts` is something the server returned.

## 2. Colour discipline: green means supported, vermilion means contradicted/failed

This is the single rule the rest of the visual design exists to protect, and it's
written down twice in the code itself (`web/src/index.css` theme comment,
`web/src/lib/verdict.ts` doc comment) precisely so nobody dilutes it by reaching for
green or red for something unrelated.

- `--color-supported` (`#1f6f43`, a muted green) is used **only** for
  `Verdict.supported`.
- `--color-contradicted` (`#a3311f`, a vermilion/red) is used for `Verdict.contradicted`
  **and** for hard failure states (a failed/cancelled run, a form validation error, an
  `ApiError`).
- Every other verdict (`partial`, `unsupported`, `no_evidence`, `stale`) renders in the
  neutral ink/bronze palette, not a third colour. `verdictTone()` in `lib/verdict.ts` is
  the single chokepoint that maps a `Verdict` to one of exactly three tones —
  `'supported' | 'contradicted' | 'neutral'` — and every place that needs to colour a
  verdict (`VerdictBadge`, `VerdictDot`, the day-timeline roll-up dot, the place
  credibility tally) goes through it rather than switching on the verdict string itself.

Why this matters: an itinerary this app renders is only trustworthy because every claim
in it carries a verdict. If "unsupported" or "stale" got their own alarming colour, the
UI would be crying wolf on states that are legitimate, expected output (see the
project's own root `README.md`: *"`unsupported` is a legitimate output shipped to the
user, not a failure to hide"*). Reserving the two signal colours for the two verdicts
that actually demand attention — "this is solid" / "this is wrong" — keeps them
meaningful. Everything else (bronze accent, ink text) is the "normal, unremarkable"
palette the eye can rest on.

**Rule for future work:** if you're tempted to add a colour for a new state, first ask
whether it's actually `supported`, `contradicted`, or neither. It is almost always
neither, and belongs in ink/bronze.

## 3. Component structure

```
web/src/
  App.tsx              route table
  main.tsx             app bootstrap (StrictMode, router, query client, dev-mock hook)
  api/
    client.ts           fetch wrapper + ApiError, one function per endpoint
    contracts.ts         hand-picked type aliases re-exported from the generated schema
    queries.ts           TanStack Query hooks (useCreateTrip, useRunSummary, useRunResult)
    schema.generated.ts  openapi-typescript output — never hand-edited (see §6)
  app/
    queryClient.ts        QueryClient factory (retry policy: no retry on 4xx, 2 retries on 5xx)
  components/            presentational + reusable pieces, one concern each
  routes/                one file per route in the table above, composes components + hooks
  schemas/
    tripBrief.ts          zod schema + form defaults, mirrors TripBriefCreate for client-side validation
  lib/                    pure formatting/derivation helpers, no React
  hooks/                  small reusable hooks (currently: useMediaQuery)
  mocks/, test/           MSW handlers/fixtures shared by unit tests, dev preview, and (now) e2e
```

The `api → app → components/routes → lib` layering is one-directional: `lib/` and
`api/` know nothing about React; `components/` know nothing about routing; `routes/`
are the only files allowed to call `useParams`/`useNavigate` and wire a page together
from components + query hooks. A component that finds itself needing route params is a
sign it should be a route, or should receive the param as a prop instead.

**`api/contracts.ts` is the seam between "what the server said" and "what the app
uses."** It doesn't redefine any shape — every exported type is a direct alias into
`ApiSchemas = components['schemas']` from the generated file — but it's the one place
allowed to `import type { components } from './schema.generated'`. Every other file
imports from `contracts.ts`, never from `schema.generated.ts` directly. That keeps a
schema regeneration (`npm run generate:api`) a one-file diff to review instead of a
grep-and-replace across the app.

`components/` are split by how reusable they are:
- Generic/dumb: `Disclosure`, `SegmentedControl`, `Stepper`, `TagInput`, `VerdictBadge`
  — no knowledge of trip/run domain types beyond `VerdictBadge`'s `Verdict` prop.
- Domain, single-purpose: `StageRoute` (progress stepper), `DayTimeline`,
  `PlaceCredibilityPanel`, `EvidenceDrawer` — each owns one region of one page.
- Shell: `AppShell` wraps every page with the header/logo/log-date chrome.
- `icons.tsx` is a hand-authored line-icon set (one stroke weight, `currentColor`) used
  everywhere instead of an icon font or a mismatched external set — the file comment
  says as much; keep new icons in that file and that style rather than pulling in an
  icon package for one glyph.

## 4. Responsive strategy: the evidence sidebar becomes a bottom sheet

`RunResultPage` lays out as `grid-cols-1` on narrow viewports and
`lg:grid-cols-[1fr_22rem]` (timeline + fixed-width aside) from `lg` up — a single
Tailwind grid, no separate mobile layout component.

`EvidenceDrawer` (built on Radix `Dialog`) is the one place with a genuine
per-breakpoint layout switch, and it's done entirely in CSS, not JS:
- Above `sm` (640px): the dialog content is pinned to the right edge
  (`fixed inset-y-0 right-0 ... sm:w-[26rem]`) and slides in from the right
  (`panel-in-right` keyframes).
- Below `sm`: `max-sm:` utility classes turn the same element into a bottom sheet
  (`inset-x-0 bottom-0 top-auto max-h-[85vh] rounded-t-2xl border-t`), and a
  `@media (max-width: 640px)` block in `index.css` swaps the animation to
  `panel-in-up`/`panel-out-down` instead of the horizontal slide.
- `@media (prefers-reduced-motion: reduce)` turns off all three animated surfaces
  (`disclosure-body`, `evidence-overlay`, `evidence-panel`) — motion-preference is
  handled once, in CSS, rather than per-component.

This is intentionally CSS-only: no `ResizeObserver`/`matchMedia` branch decides "which
component to render," so there's no layout flash while JS hydrates and no risk of the
mobile and desktop variants drifting into different DOM/behaviour. `useMediaQuery`
(`web/src/hooks/useMediaQuery.ts`) exists as a small `useSyncExternalStore`-based hook
for the rare case a future feature needs to branch in JS (e.g. changing what data it
fetches, not just how it's laid out) — it is not currently used by the drawer or
anywhere else, and should stay unused for view-only breakpoint changes; reach for it
only when a decision can't be expressed in CSS.

**Rule for future work:** new responsive behaviour should default to Tailwind
breakpoint classes / CSS media queries on the existing element. Only reach for
`useMediaQuery` (or introduce a second component tree) when the mobile and desktop
cases need genuinely different behaviour, not just different layout.

## 5. Form validation

`TripInputPage` uses `react-hook-form` + `zodResolver(tripBriefSchema)`
(`web/src/schemas/tripBrief.ts`). The zod schema mirrors `TripBriefCreate` field-for-field
(including the cross-field `end_date >= start_date` refinement) so obviously-invalid
submissions are caught client-side before a request goes out. It is deliberately not
the source of truth, though: `onSubmit` also maps server-side 422 `ValidationIssue[]`
(from `ApiError.issues`) back onto the same form fields via `setError`, because the
server's `TripBriefCreate` (Pydantic, `extra="forbid"`) is the real contract and can
reject things the client schema didn't anticipate. Client validation is a fast path,
not a substitute for handling server validation errors.

## 6. Generated API types

`npm run generate:api` runs `openapi-typescript` against a live server's
`/openapi.json` and overwrites `src/api/schema.generated.ts`. That file is
machine-generated and should never be hand-edited — if the server contract changes,
regenerate it (see §7 for how this was actually re-verified against a running backend)
and let `contracts.ts` fail to compile wherever the app used a field that moved or
disappeared. That compile failure is the intended signal, not a bug to work around by
patching the generated file.

## 7. Rules to keep consistent going forward

1. **Verdict colour is binary-plus-neutral.** New states get ink/bronze unless they are
   literally `supported` or `contradicted`. Route all verdict → colour decisions through
   `verdictTone()`.
2. **Three routes, one direction.** A run moves input → progress → result and never
   backward through the app's own state; don't add a way to "edit and resubmit" a run
   in place — that's a new trip (new `POST /trips`).
3. **`contracts.ts` is the only import surface for server types.** Never import
   `schema.generated.ts` from a component, route, or lib file.
4. **`lib/` stays pure.** No React, no fetch, no DOM — formatting/derivation only, so it
   stays trivially unit-testable (see `web/src/lib/*` and their tests).
5. **New responsive behaviour is CSS-first.** Prefer Tailwind breakpoints; only add a
   `useMediaQuery` branch when the behaviour (not just the layout) genuinely differs.
6. **Icons come from `components/icons.tsx`.** One stroke weight, `currentColor`, no
   icon font/package for a single glyph.
7. **Every fixture used in tests/mocks/dev-preview is schema-typed** (`satisfies
   RunResultView`, etc. in `web/src/test/fixtures.ts`) — a fixture that doesn't compile
   against the generated schema is a signal the fixture (or the schema) is wrong, not
   something to silence with `as any`.
