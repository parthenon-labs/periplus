import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, test } from 'vitest'
import App from './App'
import { createQueryClient } from './app/queryClient'
import { succeededResult, succeededRunSummary } from './test/fixtures'

function renderApp(initialEntries: string[]) {
  const queryClient = createQueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App routing', () => {
  test('renders the trip input page at /', () => {
    renderApp(['/'])
    expect(screen.getByRole('button', { name: /chart this trip/i })).toBeInTheDocument()
  })

  test('redirects an unmatched path back to the trip input page', () => {
    renderApp(['/somewhere/unexpected'])
    expect(screen.getByRole('button', { name: /chart this trip/i })).toBeInTheDocument()
  })

  test('renders the run progress page for /runs/:runId', async () => {
    renderApp(['/runs/run-running-research'])
    expect(await screen.findByRole('heading', { name: 'Lisbon' })).toBeInTheDocument()
  })

  test('shows the live source count while research is running', async () => {
    renderApp(['/runs/run-running-research'])
    expect(await screen.findByText('5 of 12 processed')).toBeInTheDocument()
  })

  test('renders the run result page for /runs/:runId/result', async () => {
    renderApp([`/runs/${succeededRunSummary.id}/result`])
    expect(
      await screen.findByRole('heading', { name: succeededResult.run!.itinerary!.destination! }),
    ).toBeInTheDocument()
  })

  test('the result page links to the illustrated article', async () => {
    renderApp([`/runs/${succeededRunSummary.id}/result`])
    expect(await screen.findByRole('link', { name: /read the illustrated article/i })).toHaveAttribute(
      'href',
      `/runs/${succeededRunSummary.id}/article`,
    )
  })

  test('renders the illustrated article page for /runs/:runId/article', async () => {
    renderApp([`/runs/${succeededRunSummary.id}/article`])
    const articlePiece = succeededResult.run!.edited!.pieces.find((piece) => piece.kind === 'article')!
    const firstImage = succeededResult.run!.illustrated!.images[0]!
    expect(await screen.findByRole('heading', { name: articlePiece.title! })).toBeInTheDocument()
    expect(screen.getByAltText(firstImage.subject)).toBeInTheDocument()
  })
})
