import type {
  RunResultView,
  RunSummary,
  Stage,
  StageProgress,
  TripBriefCreate,
  ValidationIssue,
} from '../api/contracts'

const CREATED_AT = '2026-08-11T08:00:00Z'
const FINISHED_AT = '2026-08-11T08:04:00Z'

export const stageOrder = [
  'research',
  'verify',
  'plan',
  'write',
  'edit',
  'illustrate',
] as const satisfies readonly Stage[]

const runningCounts: Record<Stage, RunSummary['counts']> = {
  research: {
    evidence: 2,
    claims: 2,
    verified_claims: 0,
    itinerary_items: 0,
    content_pieces: 0,
  },
  verify: {
    evidence: 4,
    claims: 3,
    verified_claims: 1,
    itinerary_items: 0,
    content_pieces: 0,
  },
  plan: {
    evidence: 4,
    claims: 3,
    verified_claims: 3,
    itinerary_items: 1,
    content_pieces: 0,
  },
  write: {
    evidence: 4,
    claims: 3,
    verified_claims: 3,
    itinerary_items: 2,
    content_pieces: 1,
  },
  edit: {
    evidence: 4,
    claims: 3,
    verified_claims: 3,
    itinerary_items: 2,
    content_pieces: 1,
  },
  illustrate: {
    evidence: 4,
    claims: 3,
    verified_claims: 3,
    itinerary_items: 2,
    content_pieces: 1,
  },
}

// Only research and verify report incremental progress today (see StageRun in
// periplus/models.py); everything else stays null here, same as the real API.
const MID_STAGE_PROGRESS: Partial<Record<Stage, { progress_current: number; progress_total: number }>> = {
  research: { progress_current: 5, progress_total: 12 },
  verify: { progress_current: 30, progress_total: 50 },
}

function progressAt(currentStage: Stage): StageProgress[] {
  const currentIndex = stageOrder.indexOf(currentStage)

  return stageOrder.map((stage, index) => ({
    stage,
    status: index < currentIndex ? 'succeeded' : index === currentIndex ? 'running' : 'pending',
    attempt: index <= currentIndex ? 1 : null,
    started_at: index <= currentIndex ? `2026-08-11T08:0${index}:00Z` : null,
    finished_at: index < currentIndex ? `2026-08-11T08:0${index + 1}:00Z` : null,
    error: null,
    progress_current: index === currentIndex ? (MID_STAGE_PROGRESS[stage]?.progress_current ?? null) : null,
    progress_total: index === currentIndex ? (MID_STAGE_PROGRESS[stage]?.progress_total ?? null) : null,
  }))
}

function runningSummary(currentStage: Stage): RunSummary {
  return {
    id: `run-running-${currentStage}`,
    status: 'running',
    error: null,
    brief: {
      destination: 'Lisbon',
      start_date: '2026-09-14',
      end_date: '2026-09-16',
    },
    current_stage: currentStage,
    stages: progressAt(currentStage),
    counts: runningCounts[currentStage],
    created_at: CREATED_AT,
    finished_at: null,
  }
}

export const runningStageSummaries = {
  research: runningSummary('research'),
  verify: runningSummary('verify'),
  plan: runningSummary('plan'),
  write: runningSummary('write'),
  edit: runningSummary('edit'),
  illustrate: runningSummary('illustrate'),
} satisfies Record<Stage, RunSummary>

export const succeededRunSummary = {
  id: 'run-succeeded',
  status: 'succeeded',
  error: null,
  brief: {
    destination: 'Lisbon',
    start_date: '2026-09-14',
    end_date: '2026-09-16',
  },
  current_stage: null,
  stages: stageOrder.map((stage, index) => ({
    stage,
    status: 'succeeded' as const,
    attempt: 1,
    started_at: `2026-08-11T08:0${index}:00Z`,
    finished_at: `2026-08-11T08:0${index + 1}:00Z`,
    error: null,
  })),
  counts: {
    evidence: 2,
    claims: 2,
    verified_claims: 2,
    itinerary_items: 2,
    content_pieces: 1,
  },
  created_at: CREATED_AT,
  finished_at: FINISHED_AT,
} satisfies RunSummary

export const failedRunSummary = {
  id: 'run-failed',
  status: 'failed',
  error: 'Verification produced no usable claims.',
  brief: {
    destination: 'Reykjavik',
    start_date: '2026-11-03',
    end_date: '2026-11-05',
  },
  current_stage: 'verify',
  stages: [
    {
      stage: 'research',
      status: 'succeeded',
      attempt: 1,
      started_at: '2026-08-11T09:00:00Z',
      finished_at: '2026-08-11T09:01:00Z',
      error: null,
    },
    {
      stage: 'verify',
      status: 'failed',
      attempt: 1,
      started_at: '2026-08-11T09:01:00Z',
      finished_at: '2026-08-11T09:02:00Z',
      error: 'Verification produced no usable claims.',
    },
    ...stageOrder.slice(2).map((stage) => ({
      stage,
      status: 'pending' as const,
      attempt: null,
      started_at: null,
      finished_at: null,
      error: null,
    })),
  ],
  counts: {
    evidence: 1,
    claims: 1,
    verified_claims: 0,
    itinerary_items: 0,
    content_pieces: 0,
  },
  created_at: '2026-08-11T09:00:00Z',
  finished_at: '2026-08-11T09:02:00Z',
} satisfies RunSummary

export const succeededResult = {
  id: succeededRunSummary.id,
  status: 'succeeded',
  error: null,
  run: {
    id: succeededRunSummary.id,
    brief: {
      id: 'brief-lisbon',
      created_at: CREATED_AT,
      destination: 'Lisbon',
      start_date: '2026-09-14',
      end_date: '2026-09-16',
      party: {
        adults: 2,
        children: 0,
        child_ages: [],
        mobility_notes: null,
      },
      budget: {
        currency: 'EUR',
        total: 900,
        per_day: 300,
      },
      interests: ['architecture', 'food'],
      must_see: ['Mosteiro dos Jerónimos'],
      avoid: [],
      dietary: [],
      pace: 'balanced',
      base_location: 'Baixa',
      language: 'en',
      notes: null,
    },
    status: 'succeeded',
    stages: stageOrder.map((stage, index) => ({
      stage,
      status: 'succeeded' as const,
      attempt: 1,
      started_at: `2026-08-11T08:0${index}:00Z`,
      finished_at: `2026-08-11T08:0${index + 1}:00Z`,
      error: null,
      calls: [],
      queries: stage === 'research' ? 2 : 0,
      fetches: stage === 'research' ? 2 : 0,
      tokens: 0,
    })),
    research: {
      brief_id: 'brief-lisbon',
      places: [
        {
          id: 'place-jeronimos',
          name: 'Mosteiro dos Jerónimos',
          kind: 'sight',
          address: 'Praça do Império, Lisbon',
          point: { lat: 38.6979, lon: -9.2065 },
          summary: 'A monastery in Belém.',
          claim_ids: ['claim-hours', 'claim-ticket'],
          typical_duration_minutes: 90,
          tags: ['architecture'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: null,
        },
        {
          id: 'claim-ticket',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The standard admission price is €18.',
          kind: 'price',
          evidence_ids: ['evidence-official-ticket'],
          check: null,
        },
      ],
      evidence: [
        {
          id: 'evidence-official-hours',
          url: 'https://example.test/jeronimos/hours',
          title: 'Visitor information',
          snippet: 'Tuesday to Sunday: 10:00–18:00.',
          source_kind: 'official',
          published_at: '2026-08-01',
          fetched_at: CREATED_AT,
          query: 'Jerónimos official opening hours',
        },
        {
          id: 'evidence-official-ticket',
          url: 'https://example.test/jeronimos/tickets',
          title: 'Tickets',
          snippet: 'Standard admission: €18.',
          source_kind: 'official',
          published_at: '2026-08-01',
          fetched_at: CREATED_AT,
          query: 'Jerónimos official ticket price',
        },
      ],
      queries_run: [
        'Jerónimos official opening hours',
        'Jerónimos official ticket price',
      ],
      gaps: [],
      created_at: '2026-08-11T08:01:00Z',
    },
    verified: {
      brief_id: 'brief-lisbon',
      places: [
        {
          id: 'place-jeronimos',
          name: 'Mosteiro dos Jerónimos',
          kind: 'sight',
          address: 'Praça do Império, Lisbon',
          point: { lat: 38.6979, lon: -9.2065 },
          summary: 'A monastery in Belém.',
          claim_ids: ['claim-hours', 'claim-ticket'],
          typical_duration_minutes: 90,
          tags: ['architecture'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: {
            verdict: 'supported',
            confidence: 0.98,
            reason: 'The official visitor information gives a 10:00 opening time.',
            supporting_evidence_ids: ['evidence-official-hours'],
            conflicting_evidence_ids: [],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
        {
          id: 'claim-ticket',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The standard admission price is €18.',
          kind: 'price',
          evidence_ids: ['evidence-official-ticket'],
          check: {
            verdict: 'contradicted',
            confidence: 0.99,
            reason: 'The official ticket page lists a different standard price.',
            supporting_evidence_ids: [],
            conflicting_evidence_ids: ['evidence-official-ticket'],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
      ],
      evidence: [
        {
          id: 'evidence-official-hours',
          url: 'https://example.test/jeronimos/hours',
          title: 'Visitor information',
          snippet: 'Tuesday to Sunday: 10:00–18:00.',
          source_kind: 'official',
          published_at: '2026-08-01',
          fetched_at: CREATED_AT,
          query: 'Jerónimos official opening hours',
        },
        {
          id: 'evidence-official-ticket',
          url: 'https://example.test/jeronimos/tickets',
          title: 'Tickets',
          snippet: 'Standard admission: €15.',
          source_kind: 'official',
          published_at: '2026-08-01',
          fetched_at: CREATED_AT,
          query: 'Jerónimos official ticket price',
        },
      ],
      queries_run: [
        'Jerónimos official opening hours',
        'Jerónimos official ticket price',
      ],
      gaps: [],
      created_at: '2026-08-11T08:01:00Z',
      verified_at: '2026-08-11T08:02:00Z',
    },
    itinerary: {
      id: 'itinerary-lisbon',
      brief_id: 'brief-lisbon',
      destination: 'Lisbon',
      days: [
        {
          day: 1,
          date: '2026-09-14',
          theme: 'Belém',
          items: [
            {
              id: 'item-jeronimos',
              day: 1,
              start: '10:00',
              end: '11:30',
              title: 'Visit Mosteiro dos Jerónimos',
              place_id: 'place-jeronimos',
              notes: 'Verify ticket price before booking.',
              claim_ids: ['claim-hours'],
              transfer_in: null,
              booking_required: true,
              estimated_cost: null,
            },
          ],
        },
      ],
      places: [
        {
          id: 'place-jeronimos',
          name: 'Mosteiro dos Jerónimos',
          kind: 'sight',
          address: 'Praça do Império, Lisbon',
          point: { lat: 38.6979, lon: -9.2065 },
          summary: 'A monastery in Belém.',
          claim_ids: ['claim-hours', 'claim-ticket'],
          typical_duration_minutes: 90,
          tags: ['architecture'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: {
            verdict: 'supported',
            confidence: 0.98,
            reason: 'The official visitor information gives a 10:00 opening time.',
            supporting_evidence_ids: ['evidence-official-hours'],
            conflicting_evidence_ids: [],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
      ],
      evidence: [
        {
          id: 'evidence-official-hours',
          url: 'https://example.test/jeronimos/hours',
          title: 'Visitor information',
          snippet: 'Tuesday to Sunday: 10:00–18:00.',
          source_kind: 'official',
          published_at: '2026-08-01',
          fetched_at: CREATED_AT,
          query: 'Jerónimos official opening hours',
        },
      ],
      caveats: ['The ticket-price claim was contradicted; confirm the current price.'],
      created_at: '2026-08-11T08:03:00Z',
    },
    content: {
      itinerary_id: 'itinerary-lisbon',
      pieces: [
        {
          id: 'piece-article',
          kind: 'article',
          title: 'Belém, on foot and on the record',
          body: 'Belém in September is a slow, golden-lit stretch of riverside worth savouring. Begin at 10:00. The Mosteiro dos Jerónimos anchors the morning.',
          word_count: 24,
          claim_ids: ['claim-hours'],
        },
        {
          id: 'piece-itinerary',
          kind: 'itinerary_doc',
          title: 'Lisbon, evidence first',
          body: 'Begin in Belém at 10:00. Confirm the current ticket price before booking.',
          word_count: 13,
          claim_ids: ['claim-hours'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: {
            verdict: 'supported',
            confidence: 0.98,
            reason: 'The official visitor information gives a 10:00 opening time.',
            supporting_evidence_ids: ['evidence-official-hours'],
            conflicting_evidence_ids: [],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
      ],
      caveats: ['The contradicted ticket-price claim was not stated as fact.'],
      edited: false,
      created_at: FINISHED_AT,
    },
    // Editor cut the "slow, golden-lit stretch" scene-setting sentence — pure atmosphere,
    // no price, route or duration behind it — and left the rest untouched.
    edited: {
      itinerary_id: 'itinerary-lisbon',
      pieces: [
        {
          id: 'piece-article',
          kind: 'article',
          title: 'Belém, on foot and on the record',
          body: 'Begin in Belém at 10:00. The Mosteiro dos Jerónimos anchors the morning.',
          word_count: 12,
          claim_ids: ['claim-hours'],
        },
        {
          id: 'piece-itinerary',
          kind: 'itinerary_doc',
          title: 'Lisbon, evidence first',
          body: 'Begin in Belém at 10:00. Confirm the current ticket price before booking.',
          word_count: 13,
          claim_ids: ['claim-hours'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: {
            verdict: 'supported',
            confidence: 0.98,
            reason: 'The official visitor information gives a 10:00 opening time.',
            supporting_evidence_ids: ['evidence-official-hours'],
            conflicting_evidence_ids: [],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
      ],
      caveats: [
        'The contradicted ticket-price claim was not stated as fact.',
        "Cut a sentence of pure atmosphere from the 'article' piece: no price, route, or duration behind it.",
      ],
      edited: true,
      created_at: FINISHED_AT,
    },
    illustrated: {
      itinerary_id: 'itinerary-lisbon',
      pieces: [
        {
          id: 'piece-article',
          kind: 'article',
          title: 'Belém, on foot and on the record',
          body: 'Begin in Belém at 10:00. The Mosteiro dos Jerónimos anchors the morning.',
          word_count: 12,
          claim_ids: ['claim-hours'],
        },
        {
          id: 'piece-itinerary',
          kind: 'itinerary_doc',
          title: 'Lisbon, evidence first',
          body: 'Begin in Belém at 10:00. Confirm the current ticket price before booking.',
          word_count: 13,
          claim_ids: ['claim-hours'],
        },
      ],
      claims: [
        {
          id: 'claim-hours',
          subject: 'Mosteiro dos Jerónimos',
          text: 'The monastery opens at 10:00 on Tuesday.',
          kind: 'hours',
          evidence_ids: ['evidence-official-hours'],
          check: {
            verdict: 'supported',
            confidence: 0.98,
            reason: 'The official visitor information gives a 10:00 opening time.',
            supporting_evidence_ids: ['evidence-official-hours'],
            conflicting_evidence_ids: [],
            model: 'scripted-test-model',
            checked_at: '2026-08-11T08:02:00Z',
          },
        },
      ],
      caveats: [],
      edited: true,
      created_at: FINISHED_AT,
      images: [
        {
          id: 'image-jeronimos',
          subject: 'Mosteiro dos Jerónimos',
          claim_ids: ['claim-hours'],
          prompt: 'Editorial travel photograph of Mosteiro dos Jerónimos.',
          data_base64: 'aGVsbG8=',
          mime_type: 'image/png',
          model: 'scripted-image-model',
          created_at: FINISHED_AT,
        },
      ],
    },
    created_at: CREATED_AT,
    finished_at: FINISHED_AT,
  },
} satisfies RunResultView

export const emptyResult = {
  id: 'run-empty',
  status: 'succeeded',
  error: null,
  run: {
    id: 'run-empty',
    brief: {
      id: 'brief-empty',
      created_at: CREATED_AT,
      destination: 'Lisbon',
      start_date: '2026-09-14',
      end_date: '2026-09-16',
      party: { adults: 1, children: 0, child_ages: [], mobility_notes: null },
      budget: { currency: 'EUR', total: null, per_day: null },
      interests: [],
      must_see: [],
      avoid: [],
      dietary: [],
      pace: 'balanced',
      base_location: null,
      language: 'en',
      notes: null,
    },
    status: 'succeeded',
    stages: [],
    research: {
      brief_id: 'brief-empty',
      places: [],
      claims: [],
      evidence: [],
      queries_run: [],
      gaps: [],
      created_at: CREATED_AT,
    },
    verified: {
      brief_id: 'brief-empty',
      places: [],
      claims: [],
      evidence: [],
      queries_run: [],
      gaps: [],
      created_at: CREATED_AT,
      verified_at: CREATED_AT,
    },
    itinerary: {
      id: 'itinerary-empty',
      brief_id: 'brief-empty',
      destination: 'Lisbon',
      days: [],
      places: [],
      claims: [],
      evidence: [],
      caveats: [],
      created_at: CREATED_AT,
    },
    content: {
      itinerary_id: 'itinerary-empty',
      pieces: [],
      caveats: [],
      edited: false,
      created_at: CREATED_AT,
    },
    created_at: CREATED_AT,
    finished_at: CREATED_AT,
  },
} satisfies RunResultView

export const tripBriefInput = {
  destination: 'Lisbon',
  start_date: '2026-09-14',
  end_date: '2026-09-16',
  party: { adults: 2, children: 0, child_ages: [], mobility_notes: null },
  budget: { currency: 'EUR', total: 900, per_day: 300 },
  interests: ['architecture', 'food'],
  must_see: ['Mosteiro dos Jerónimos'],
  avoid: [],
  dietary: [],
  pace: 'balanced',
  base_location: 'Baixa',
  language: 'en',
  notes: null,
} satisfies TripBriefCreate

export const invalidTripBriefInput = {
  ...tripBriefInput,
  destination: '',
} satisfies TripBriefCreate

export const notFoundResponse = {
  detail: 'Run not found',
}

export const validationIssues = [
  {
    loc: ['body', 'destination'],
    msg: 'String should have at least 1 character',
    type: 'string_too_short',
    input: '',
  },
] satisfies ValidationIssue[]

export const validationErrorResponse = {
  detail: validationIssues,
}
