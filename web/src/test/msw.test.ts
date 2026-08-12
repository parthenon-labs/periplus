import { describe, expect, test } from 'vitest'
import { api, ApiError } from '../api/client'
import {
  emptyResult,
  failedRunSummary,
  invalidTripBriefInput,
  runningStageSummaries,
  succeededResult,
  validationIssues,
} from './fixtures'

describe('MSW API foundation', () => {
  test('serves a deterministic running-stage summary', async () => {
    const expected = runningStageSummaries.verify

    await expect(api.getRun(expected.id)).resolves.toEqual(expected)
  })

  test('serves succeeded and empty result fixtures', async () => {
    await expect(api.getResult(succeededResult.id)).resolves.toEqual(succeededResult)
    await expect(api.getResult(emptyResult.id)).resolves.toEqual(emptyResult)
  })

  test('serves a failed run fixture', async () => {
    await expect(api.getRun(failedRunSummary.id)).resolves.toEqual(failedRunSummary)
  })

  test('turns the deterministic 404 response into ApiError', async () => {
    await expect(api.getRun('run-missing')).rejects.toEqual(
      expect.objectContaining<Partial<ApiError>>({
        name: 'ApiError',
        status: 404,
        message: 'Run not found',
      }),
    )
  })

  test('turns the deterministic 422 response into ApiError issues', async () => {
    await expect(api.createTrip(invalidTripBriefInput)).rejects.toEqual(
      expect.objectContaining<Partial<ApiError>>({
        name: 'ApiError',
        status: 422,
        issues: validationIssues,
      }),
    )
  })
})
