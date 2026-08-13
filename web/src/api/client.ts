import type {
  RunResultView,
  RunSummary,
  TripBriefCreate,
  ValidationIssue,
} from './contracts'
import {
  failedRunSummary,
  runningStageSummaries,
  succeededResult,
  succeededRunSummary,
} from '../test/fixtures'

const isDemo = import.meta.env.MODE === 'demo'

const demoSummaries = [
  succeededRunSummary,
  ...Object.values(runningStageSummaries),
  failedRunSummary,
]

export class ApiError extends Error {
  readonly status: number
  readonly issues: ValidationIssue[]

  constructor(message: string, status: number, issues: ValidationIssue[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.issues = issues
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`
    let issues: ValidationIssue[] = []
    try {
      const body = (await response.json()) as {
        detail?: string | ValidationIssue[]
      }
      if (typeof body.detail === 'string') message = body.detail
      if (Array.isArray(body.detail)) {
        issues = body.detail
        message = body.detail.map((issue) => issue.msg).join(', ')
      }
    } catch {
      // The status remains useful even when an upstream error is not JSON.
    }
    throw new ApiError(message, response.status, issues)
  }

  return response.json() as Promise<T>
}

interface ApiClient {
  createTrip: (brief: TripBriefCreate) => Promise<RunSummary>
  listRuns: () => Promise<RunSummary[]>
  getRun: (runId: string) => Promise<RunSummary>
  getResult: (runId: string) => Promise<RunResultView>
}

export const api: ApiClient = {
  createTrip: (brief: TripBriefCreate) =>
    isDemo ? Promise.resolve(succeededRunSummary) : request<RunSummary>('/trips', {
      method: 'POST',
      body: JSON.stringify(brief),
    }),
  listRuns: () => isDemo ? Promise.resolve(demoSummaries) : request<RunSummary[]>('/runs'),
  getRun: (runId: string) => {
    if (!isDemo) return request<RunSummary>(`/runs/${runId}`)
    const summary = demoSummaries.find((candidate) => candidate.id === runId)
    return summary
      ? Promise.resolve(summary)
      : Promise.reject(new ApiError('Run not found', 404))
  },
  getResult: (runId: string) => {
    if (!isDemo) return request<RunResultView>(`/runs/${runId}/result`)
    return runId === succeededResult.id
      ? Promise.resolve(succeededResult)
      : Promise.reject(new ApiError('Run result not found', 404))
  },
}
