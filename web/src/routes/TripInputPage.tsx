import { zodResolver } from '@hookform/resolvers/zod'
import { useEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { Controller, useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { AppShell } from '../components/AppShell'
import { DatePicker } from '../components/DatePicker'
import { Disclosure } from '../components/Disclosure'
import { SegmentedControl } from '../components/SegmentedControl'
import { Stepper } from '../components/Stepper'
import { TagInput } from '../components/TagInput'
import { AnchorRouteIcon, CoinIcon, CompassIcon, PenIcon, PeopleIcon, StarIcon } from '../components/icons'
import { useCreateTrip } from '../api/queries'
import { formatNights } from '../lib/format'
import { defaultTripBriefValues, tripBriefSchema, type TripBriefFormValues } from '../schemas/tripBrief'

// The static GitHub Pages build ships one real, previously captured pipeline run
// (Tokyo, Nov 2026 — see `succeededResult` in src/test/fixtures.ts). There is no
// backend behind it, so any brief the visitor types would still return that same
// run. Rather than let the form imply otherwise, demo mode pins every field to
// the brief that actually produced it and locks editing. Values are literals here
// on purpose: importing fixtures.ts would pull ~1.7MB of embedded data into the
// production bundle too (same reasoning as AppShell's DEMO_SAMPLE_RUN_ID).
const isDemo = import.meta.env.MODE === 'demo'

const demoTripBriefValues: TripBriefFormValues = {
  destination: 'Tokyo, Japan',
  start_date: '2026-11-10',
  end_date: '2026-11-14',
  party: { adults: 2, children: 0, child_ages: [], mobility_notes: null },
  budget: { currency: 'USD', total: null, per_day: 250 },
  interests: ['food', 'culture', 'temples'],
  must_see: ['Senso-ji Temple', 'Shibuya Crossing'],
  avoid: [],
  dietary: [],
  pace: 'balanced',
  base_location: 'Shinjuku',
  language: 'en',
  notes:
    'First trip to Japan, celebrating an anniversary, want a mix of iconic sights and authentic local food.',
}

const lockedFieldClass = isDemo ? 'cursor-not-allowed text-ink-soft' : ''

const prefersReducedMotion =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Untouched number inputs report their default (`null`), not `''` — `Number(null)` is `0`,
// which would silently pass a blank budget field off as "$0". Treat both as "not set".
function parseOptionalNumber(value: unknown): number | null {
  if (value === '' || value === null || value === undefined) return null
  return Number(value)
}

export function TripInputPage() {
  const navigate = useNavigate()
  const createTrip = useCreateTrip()

  const {
    register,
    handleSubmit,
    control,
    watch,
    setError,
    formState: { errors },
  } = useForm<TripBriefFormValues>({
    resolver: zodResolver(tripBriefSchema),
    defaultValues: isDemo ? demoTripBriefValues : defaultTripBriefValues,
  })

  const startDate = watch('start_date')
  const endDate = watch('end_date')
  const nights = formatNights(startDate, endDate)
  const nightsRef = useRef<HTMLSpanElement>(null)
  const compassRef = useRef<SVGSVGElement>(null)

  // The nights count is a derived relationship, not raw input — give it a
  // small arrival so the connection between the two dates reads as cause and
  // effect rather than a value that was simply always there.
  useEffect(() => {
    if (nights === null || !nightsRef.current || prefersReducedMotion) return
    gsap.fromTo(
      nightsRef.current,
      { opacity: 0, y: -3 },
      { opacity: 1, y: 0, duration: 0.22, ease: 'power2.out' },
    )
  }, [nights])

  useEffect(() => {
    const node = compassRef.current
    if (!node) return
    if (createTrip.isPending && !prefersReducedMotion) {
      const tween = gsap.to(node, { rotate: 360, duration: 1.1, repeat: -1, ease: 'none', transformOrigin: '50% 50%' })
      return () => {
        tween.kill()
        gsap.set(node, { rotate: 0 })
      }
    }
    return undefined
  }, [createTrip.isPending])

  const onSubmit = handleSubmit(async (values) => {
    try {
      const run = await createTrip.mutateAsync(values)
      navigate(`/runs/${run.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.issues.length > 0) {
        for (const issue of err.issues) {
          const path = issue.loc.filter((p) => p !== 'body').join('.')
          if (path) setError(path as keyof TripBriefFormValues, { message: issue.msg })
        }
      }
    }
  })

  return (
    <AppShell>
      <form onSubmit={onSubmit} className="mx-auto flex max-w-3xl flex-col gap-10 px-5 pb-24 pt-10 sm:px-8 sm:pt-16">
        {isDemo ? (
          <aside
            id="demo-notice"
            className="rounded-2xl border border-bronze-deep/30 bg-bronze-pale px-5 py-4 text-sm leading-relaxed text-bronze-deep sm:px-6"
          >
            <p className="font-medium">Static demo — no backend is running.</p>
            <p className="mt-1.5 text-ink-soft">
              This site ships a single real pipeline run: a four-night Tokyo trip researched, verified, planned
              and written on 14 Aug 2026. The brief below is the one that produced it, pinned and read-only —
              a different destination here could not produce a different itinerary, so the form does not
              pretend it could. Submitting opens that finished run.
            </p>
            <p className="mt-1.5 text-ink-soft">
              To run your own brief, clone the repo and start the API — see the README.
            </p>
          </aside>
        ) : null}

        <section className="chart-grid rounded-2xl border border-line px-6 py-10 sm:px-10 sm:py-14">
          <label className="flex flex-col gap-2">
            <span className="text-sm font-medium text-bronze-deep">Destination</span>
            <input
              {...register('destination')}
              placeholder="Where to?"
              autoFocus={!isDemo}
              readOnly={isDemo}
              aria-describedby={isDemo ? 'demo-notice' : undefined}
              className={`w-full border-b-2 border-line-strong bg-transparent font-serif text-4xl text-ink outline-none placeholder:text-ink-faint focus:border-bronze-deep sm:text-5xl ${lockedFieldClass}`}
            />
            {errors.destination ? <span className="text-sm text-contradicted">{errors.destination.message}</span> : null}
          </label>

          <div className="mt-8 flex flex-wrap items-end gap-6">
            <Controller
              control={control}
              name="start_date"
              render={({ field }) => (
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="start_date" className="text-sm font-medium text-ink">
                    Depart
                  </label>
                  <DatePicker
                    id="start_date"
                    label="Departure date"
                    value={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                </div>
              )}
            />
            <Controller
              control={control}
              name="end_date"
              render={({ field }) => (
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="end_date" className="text-sm font-medium text-ink">
                    Return
                  </label>
                  <DatePicker
                    id="end_date"
                    label="Return date"
                    value={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                </div>
              )}
            />
            {nights !== null && nights > 0 ? (
              <span ref={nightsRef} className="chart-tick tabular pb-2.5">
                {nights} night{nights === 1 ? '' : 's'}
              </span>
            ) : null}
          </div>
          {errors.start_date ? <p className="mt-2 text-sm text-contradicted">{errors.start_date.message}</p> : null}
          {errors.end_date ? <p className="mt-2 text-sm text-contradicted">{errors.end_date.message}</p> : null}
        </section>

        <section className="rounded-2xl border border-line px-6 sm:px-8">
          {isDemo ? (
            <p className="border-b border-line py-4 text-sm text-ink-soft">
              The panels below hold the rest of that same brief — party, budget, interests, pace and notes as
              they were actually submitted. They are shown read-only so the inputs on this page always match
              the itinerary they produced.
            </p>
          ) : null}
          <Disclosure title="Who's travelling" subtitle="Party size and any access notes" icon={PeopleIcon}>
            <div className="flex flex-col gap-3">
              <Controller
                control={control}
                name="party.adults"
                render={({ field }) => (
                  <Stepper label="Adults" value={field.value} min={1} onChange={field.onChange} disabled={isDemo} />
                )}
              />
              <Controller
                control={control}
                name="party.children"
                render={({ field }) => (
                  <Stepper label="Children" value={field.value} min={0} onChange={field.onChange} disabled={isDemo} />
                )}
              />
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Mobility notes</span>
                <textarea
                  {...register('party.mobility_notes')}
                  rows={2}
                  readOnly={isDemo}
                  placeholder={isDemo ? 'Not set for this run' : 'Anything the route should account for'}
                  className={`rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep ${lockedFieldClass}`}
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Budget" subtitle="Optional — leave blank to skip" icon={CoinIcon}>
            <div className="flex flex-wrap gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Currency</span>
                <input
                  {...register('budget.currency')}
                  readOnly={isDemo}
                  className={`w-24 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm uppercase outline-none focus:border-bronze-deep ${lockedFieldClass}`}
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Total</span>
                <input
                  type="number"
                  step="1"
                  {...register('budget.total', { setValueAs: parseOptionalNumber })}
                  readOnly={isDemo}
                  placeholder={isDemo ? 'Not set' : undefined}
                  className={`tabular w-32 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none focus:border-bronze-deep ${lockedFieldClass}`}
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Per day</span>
                <input
                  type="number"
                  step="1"
                  {...register('budget.per_day', { setValueAs: parseOptionalNumber })}
                  readOnly={isDemo}
                  className={`tabular w-32 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none focus:border-bronze-deep ${lockedFieldClass}`}
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Interests &amp; priorities" subtitle="What the route should lean into or steer around" icon={StarIcon}>
            <div className="flex flex-col gap-4">
              <Controller
                control={control}
                name="interests"
                render={({ field }) => (
                  <TagInput
                    label="Interests"
                    placeholder="architecture, food…"
                    values={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                )}
              />
              <Controller
                control={control}
                name="must_see"
                render={({ field }) => (
                  <TagInput
                    label="Must see"
                    placeholder="A specific place to guarantee"
                    values={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                )}
              />
              <Controller
                control={control}
                name="avoid"
                render={({ field }) => (
                  <TagInput
                    label="Avoid"
                    placeholder="Anything to rule out"
                    values={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                )}
              />
              <Controller
                control={control}
                name="dietary"
                render={({ field }) => (
                  <TagInput
                    label="Dietary"
                    placeholder="vegetarian, halal…"
                    values={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                  />
                )}
              />
            </div>
          </Disclosure>

          <Disclosure title="Pace &amp; base" subtitle="How full the days run, and where they start" icon={AnchorRouteIcon}>
            <div className="flex flex-col gap-4">
              <Controller
                control={control}
                name="pace"
                render={({ field }) => (
                  <SegmentedControl
                    label="Pace"
                    value={field.value}
                    onChange={field.onChange}
                    disabled={isDemo}
                    options={[
                      { value: 'relaxed', label: 'Relaxed' },
                      { value: 'balanced', label: 'Balanced' },
                      { value: 'packed', label: 'Packed' },
                    ]}
                  />
                )}
              />
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Base location</span>
                <input
                  {...register('base_location')}
                  readOnly={isDemo}
                  placeholder="Hotel or neighbourhood"
                  className={`rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep ${lockedFieldClass}`}
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Notes" subtitle="Anything else worth telling the Explorer" icon={PenIcon}>
            <textarea
              {...register('notes')}
              rows={3}
              readOnly={isDemo}
              placeholder="Free-form notes"
              className={`w-full rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep ${lockedFieldClass}`}
            />
          </Disclosure>
        </section>

        {createTrip.isError && !(createTrip.error instanceof ApiError && createTrip.error.issues.length > 0) ? (
          <p className="rounded-lg border border-contradicted/30 bg-contradicted-tint px-4 py-3 text-sm text-contradicted">
            {createTrip.error instanceof Error ? createTrip.error.message : 'Something went wrong submitting the brief.'}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={createTrip.isPending}
          className="inline-flex w-fit items-center gap-2.5 self-start rounded-full bg-bronze-deep px-7 py-3 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-70"
        >
          {createTrip.isPending ? <CompassIcon ref={compassRef} className="size-4" /> : null}
          {createTrip.isPending ? 'Charting…' : isDemo ? 'Open the example run' : 'Chart this trip'}
        </button>
      </form>
    </AppShell>
  )
}
