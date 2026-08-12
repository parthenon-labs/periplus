import { http, HttpResponse } from 'msw'
import type { RunSummary, TripBriefCreate } from '../../api/contracts'
import {
  emptyResult,
  failedRunSummary,
  notFoundResponse,
  runningStageSummaries,
  succeededResult,
  succeededRunSummary,
  validationErrorResponse,
} from '../fixtures'

const runSummaries: RunSummary[] = [
  ...Object.values(runningStageSummaries),
  succeededRunSummary,
  failedRunSummary,
]

const summariesById = new Map(runSummaries.map((summary) => [summary.id, summary]))

export const handlers = [
  http.post('/api/trips', async ({ request }) => {
    const brief = (await request.json()) as TripBriefCreate

    if (!brief.destination.trim()) {
      return HttpResponse.json(validationErrorResponse, { status: 422 })
    }

    return HttpResponse.json(runningStageSummaries.research, { status: 202 })
  }),

  http.get('/api/runs', () => HttpResponse.json(runSummaries)),

  http.get('/api/runs/:runId/result', ({ params }) => {
    if (params.runId === succeededResult.id) return HttpResponse.json(succeededResult)
    if (params.runId === emptyResult.id) return HttpResponse.json(emptyResult)

    return HttpResponse.json(notFoundResponse, { status: 404 })
  }),

  http.get('/api/runs/:runId', ({ params }) => {
    const summary = summariesById.get(String(params.runId))
    if (summary) return HttpResponse.json(summary)

    return HttpResponse.json(notFoundResponse, { status: 404 })
  }),
]
