import { z } from 'zod'

export const tripBriefSchema = z
  .object({
    destination: z.string().trim().min(1, 'Enter a destination.'),
    start_date: z.string().min(1, 'Choose a start date.'),
    end_date: z.string().min(1, 'Choose an end date.'),
    party: z.object({
      adults: z.number().int().min(1, 'At least one adult.').max(16),
      children: z.number().int().min(0).max(16),
      child_ages: z.array(z.number().int().min(0).max(17)),
      mobility_notes: z.string().trim().nullable(),
    }),
    budget: z.object({
      currency: z.string().trim().min(1, 'Currency required.').max(6),
      total: z.number().positive().nullable(),
      per_day: z.number().positive().nullable(),
    }),
    interests: z.array(z.string()),
    must_see: z.array(z.string()),
    avoid: z.array(z.string()),
    dietary: z.array(z.string()),
    pace: z.enum(['relaxed', 'balanced', 'packed']),
    base_location: z.string().trim().nullable(),
    language: z.string().trim().min(1),
    notes: z.string().trim().max(1000).nullable(),
  })
  .refine((v) => v.end_date >= v.start_date, {
    message: 'End date must be on or after the start date.',
    path: ['end_date'],
  })

export type TripBriefFormValues = z.infer<typeof tripBriefSchema>

export const defaultTripBriefValues: TripBriefFormValues = {
  destination: '',
  start_date: '',
  end_date: '',
  party: { adults: 1, children: 0, child_ages: [], mobility_notes: null },
  budget: { currency: 'AUD', total: null, per_day: null },
  interests: [],
  must_see: [],
  avoid: [],
  dietary: [],
  pace: 'balanced',
  base_location: null,
  language: 'en',
  notes: null,
}
