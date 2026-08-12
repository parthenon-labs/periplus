import type {
  RunResultView,
  RunSummary,
  TripBriefCreate,
  ValidationIssue,
} from './contracts'

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

export const api = {
  createTrip: (brief: TripBriefCreate) =>
    request<RunSummary>('/trips', {
      method: 'POST',
      body: JSON.stringify(brief),
    }),
  listRuns: () => request<RunSummary[]>('/runs'),
  getRun: (runId: string) => request<RunSummary>(`/runs/${runId}`),
  getResult: (runId: string) => request<RunResultView>(`/runs/${runId}/result`),
}
