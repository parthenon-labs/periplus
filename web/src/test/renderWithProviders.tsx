import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { createQueryClient } from '../app/queryClient'

/**
 * Renders a route table (rather than a single element) inside a MemoryRouter and a
 * fresh QueryClientProvider, so route-level pages under test (which call
 * useParams/useNavigate and TanStack Query hooks) run the same as they do in the real
 * app. Each call gets an isolated QueryClient so tests never share cached data.
 */
export function renderRoutes(
  routes: { path: string; element: ReactElement }[],
  { initialEntries = ['/'] }: { initialEntries?: string[] } = {},
) {
  const queryClient = createQueryClient()

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          {routes.map((route) => (
            <Route key={route.path} path={route.path} element={route.element} />
          ))}
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )

  return { ...utils, queryClient }
}
