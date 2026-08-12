import type { QueryClient } from '@tanstack/react-query'
import { describe, expect, test } from 'vitest'
import { ApiError } from '../api/client'
import * as queryClientModule from './queryClient'

type QueryClientFactory = () => QueryClient

function queryClientFactory(): QueryClientFactory | undefined {
  const candidate = Reflect.get(queryClientModule, 'createQueryClient')
  return typeof candidate === 'function' ? (candidate as QueryClientFactory) : undefined
}

describe('createQueryClient', () => {
  test('returns clients with isolated caches', () => {
    const createQueryClient = queryClientFactory()
    expect(createQueryClient).toBeTypeOf('function')
    if (!createQueryClient) return

    const first = createQueryClient()
    const second = createQueryClient()

    first.setQueryData(['run', 'one'], { status: 'running' })

    expect(second.getQueryData(['run', 'one'])).toBeUndefined()
  })

  test('does not retry client ApiError responses', async () => {
    const createQueryClient = queryClientFactory()
    expect(createQueryClient).toBeTypeOf('function')
    if (!createQueryClient) return

    const client = createQueryClient()
    let attempts = 0

    await expect(
      client.fetchQuery({
        queryKey: ['client-error'],
        queryFn: () => {
          attempts += 1
          throw new ApiError('Invalid trip brief', 422)
        },
        retryDelay: 0,
      }),
    ).rejects.toMatchObject({ status: 422 })

    expect(attempts).toBe(1)
  })

  test('retries server ApiError responses twice', async () => {
    const createQueryClient = queryClientFactory()
    expect(createQueryClient).toBeTypeOf('function')
    if (!createQueryClient) return

    const client = createQueryClient()
    let attempts = 0

    await expect(
      client.fetchQuery({
        queryKey: ['server-error'],
        queryFn: () => {
          attempts += 1
          throw new ApiError('Service unavailable', 503)
        },
        retryDelay: 0,
      }),
    ).rejects.toMatchObject({ status: 503 })

    expect(attempts).toBe(3)
  })
})
