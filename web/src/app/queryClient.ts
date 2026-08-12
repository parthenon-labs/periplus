import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (attempt, error) => {
          if (error instanceof ApiError && error.status < 500) return false
          return attempt < 2
        },
        refetchOnWindowFocus: false,
      },
    },
  })
}

export const queryClient = createQueryClient()
