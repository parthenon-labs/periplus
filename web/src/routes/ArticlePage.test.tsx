import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import type { RunResultView } from '../api/contracts'
import { server } from '../test/mocks/server'
import { renderRoutes } from '../test/renderWithProviders'
import { succeededResult } from '../test/fixtures'
import { ArticlePage } from './ArticlePage'

function renderArticle(runId: string) {
  return renderRoutes([{ path: '/runs/:runId/article', element: <ArticlePage /> }], {
    initialEntries: [`/runs/${runId}/article`],
  })
}

describe('ArticlePage', () => {
  test('renders Editor\'s revised body, not Chronicler\'s pre-edit draft', async () => {
    renderArticle(succeededResult.id)

    expect(await screen.findByRole('heading', { name: 'Belém, on foot and on the record' })).toBeInTheDocument()
    // The pre-edit article opened with a scene-setting sentence Editor cut for stating
    // no price, route, or duration — it should not be on the page.
    expect(screen.queryByText(/slow, golden-lit stretch/i)).not.toBeInTheDocument()
    expect(screen.getByText(/Begin in Belém at 10:00\. The Mosteiro dos Jerónimos anchors the morning\./)).toBeInTheDocument()
  })

  test('shows both Editor and Illustrator caveats under one heading', async () => {
    renderArticle(succeededResult.id)

    expect(await screen.findByRole('heading', { name: 'Editing and illustration notes' })).toBeInTheDocument()
    expect(screen.getByText(/Cut a sentence of pure atmosphere/i)).toBeInTheDocument()
    expect(screen.getByText(/contradicted ticket-price claim/i)).toBeInTheDocument()
  })

  test('places an image next to the paragraph that names its subject, not a fixed position', async () => {
    renderArticle(succeededResult.id)

    const heading = await screen.findByRole('heading', { name: 'Belém, on foot and on the record' })
    const article = heading.closest('article')
    expect(article).not.toBeNull()

    const image = screen.getByAltText('Mosteiro dos Jerónimos')
    // The image must render inside the same <article> as the paragraph mentioning its
    // subject, immediately after that paragraph — not appended past unrelated copy.
    expect(article).toContainElement(image)
    const paragraph = screen.getByText(/Mosteiro dos Jerónimos anchors the morning/)
    expect(paragraph.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  test('falls back to Chronicler\'s own draft when no Editor pass exists on the run', async () => {
    const withoutEdit: RunResultView = {
      ...succeededResult,
      run: succeededResult.run ? { ...succeededResult.run, edited: null } : succeededResult.run,
    }
    server.use(
      http.get('/api/runs/:runId/result', () => HttpResponse.json(withoutEdit)),
    )

    renderArticle('run-succeeded')

    expect(await screen.findByRole('heading', { name: 'Belém, on foot and on the record' })).toBeInTheDocument()
    // Falls all the way back to the pre-edit sentence Editor would otherwise have cut.
    expect(screen.getByText(/slow, golden-lit stretch/i)).toBeInTheDocument()
  })
})
