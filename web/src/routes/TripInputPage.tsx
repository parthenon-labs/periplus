import { zodResolver } from '@hookform/resolvers/zod'
import { Controller, useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { AppShell } from '../components/AppShell'
import { Disclosure } from '../components/Disclosure'
import { SegmentedControl } from '../components/SegmentedControl'
import { Stepper } from '../components/Stepper'
import { TagInput } from '../components/TagInput'
import { useCreateTrip } from '../api/queries'
import { formatNights } from '../lib/format'
import { defaultTripBriefValues, tripBriefSchema, type TripBriefFormValues } from '../schemas/tripBrief'

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
    defaultValues: defaultTripBriefValues,
  })

  const startDate = watch('start_date')
  const endDate = watch('end_date')
  const nights = formatNights(startDate, endDate)

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
        <section className="chart-grid rounded-2xl border border-line px-6 py-10 sm:px-10 sm:py-14">
          <label className="flex flex-col gap-2">
            <span className="text-sm font-medium text-bronze-deep">Destination</span>
            <input
              {...register('destination')}
              placeholder="Where to?"
              autoFocus
              className="w-full border-b-2 border-line-strong bg-transparent font-serif text-4xl text-ink outline-none placeholder:text-ink-faint focus:border-bronze-deep sm:text-5xl"
            />
            {errors.destination ? <span className="text-sm text-contradicted">{errors.destination.message}</span> : null}
          </label>

          <div className="mt-8 flex flex-wrap items-end gap-6">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink">Depart</span>
              <input
                type="date"
                {...register('start_date')}
                className="tabular rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-bronze-deep"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink">Return</span>
              <input
                type="date"
                {...register('end_date')}
                className="tabular rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-bronze-deep"
              />
            </label>
            {nights !== null && nights > 0 ? (
              <span className="chart-tick tabular pb-2.5">
                {nights} night{nights === 1 ? '' : 's'}
              </span>
            ) : null}
          </div>
          {errors.start_date ? <p className="mt-2 text-sm text-contradicted">{errors.start_date.message}</p> : null}
          {errors.end_date ? <p className="mt-2 text-sm text-contradicted">{errors.end_date.message}</p> : null}
        </section>

        <section className="rounded-2xl border border-line px-6 sm:px-8">
          <Disclosure title="Who's travelling" subtitle="Party size and any access notes">
            <div className="flex flex-col gap-3">
              <Controller
                control={control}
                name="party.adults"
                render={({ field }) => <Stepper label="Adults" value={field.value} min={1} onChange={field.onChange} />}
              />
              <Controller
                control={control}
                name="party.children"
                render={({ field }) => <Stepper label="Children" value={field.value} min={0} onChange={field.onChange} />}
              />
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Mobility notes</span>
                <textarea
                  {...register('party.mobility_notes')}
                  rows={2}
                  placeholder="Anything the route should account for"
                  className="rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep"
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Budget" subtitle="Optional — leave blank to skip">
            <div className="flex flex-wrap gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Currency</span>
                <input
                  {...register('budget.currency')}
                  className="w-24 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm uppercase outline-none focus:border-bronze-deep"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Total</span>
                <input
                  type="number"
                  step="1"
                  {...register('budget.total', { setValueAs: parseOptionalNumber })}
                  className="tabular w-32 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none focus:border-bronze-deep"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink">Per day</span>
                <input
                  type="number"
                  step="1"
                  {...register('budget.per_day', { setValueAs: parseOptionalNumber })}
                  className="tabular w-32 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none focus:border-bronze-deep"
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Interests &amp; priorities" subtitle="What the route should lean into or steer around">
            <div className="flex flex-col gap-4">
              <Controller
                control={control}
                name="interests"
                render={({ field }) => (
                  <TagInput label="Interests" placeholder="architecture, food…" values={field.value} onChange={field.onChange} />
                )}
              />
              <Controller
                control={control}
                name="must_see"
                render={({ field }) => (
                  <TagInput label="Must see" placeholder="A specific place to guarantee" values={field.value} onChange={field.onChange} />
                )}
              />
              <Controller
                control={control}
                name="avoid"
                render={({ field }) => <TagInput label="Avoid" placeholder="Anything to rule out" values={field.value} onChange={field.onChange} />}
              />
              <Controller
                control={control}
                name="dietary"
                render={({ field }) => (
                  <TagInput label="Dietary" placeholder="vegetarian, halal…" values={field.value} onChange={field.onChange} />
                )}
              />
            </div>
          </Disclosure>

          <Disclosure title="Pace &amp; base" subtitle="How full the days run, and where they start">
            <div className="flex flex-col gap-4">
              <Controller
                control={control}
                name="pace"
                render={({ field }) => (
                  <SegmentedControl
                    label="Pace"
                    value={field.value}
                    onChange={field.onChange}
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
                  placeholder="Hotel or neighbourhood"
                  className="rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep"
                />
              </label>
            </div>
          </Disclosure>

          <Disclosure title="Notes" subtitle="Anything else worth telling the Explorer">
            <textarea
              {...register('notes')}
              rows={3}
              placeholder="Free-form notes"
              className="w-full rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none placeholder:text-ink-faint focus:border-bronze-deep"
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
          className="self-start rounded-full bg-bronze-deep px-7 py-3 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {createTrip.isPending ? 'Charting…' : 'Chart this trip'}
        </button>
      </form>
    </AppShell>
  )
}
