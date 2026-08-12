import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from './client'
import { isTerminalStatus, type TripBriefCreate } from './contracts'

export const runKeys = {
  all: ['runs'] as const,
  detail: (runId: string) => ['runs', runId] as const,
  result: (runId: string) => ['runs', runId, 'result'] as const,
}

export function useCreateTrip() {
  return useMutation({
    mutationFn: (brief: TripBriefCreate) => api.createTrip(brief),
  })
}

export function useRunSummary(runId: string) {
  return useQuery({
    queryKey: runKeys.detail(runId),
    queryFn: () => api.getRun(runId),
    refetchInterval: (query) => {
      const summary = query.state.data
      return summary && isTerminalStatus(summary.status) ? false : 1_000
    },
  })
}

export function useRunResult(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: runKeys.result(runId),
    queryFn: () => api.getResult(runId),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
}
