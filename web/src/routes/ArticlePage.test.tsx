import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import type { RunResultView } from '../api/contracts'
import { server } from '../test/mocks/server'
import { renderRoutes } from '../test/renderWithProviders'
import { ArticlePage } from './ArticlePage'

// These tests exercise ArticlePage's own rendering logic (Editor-vs-Chronicler
// fallback, caveats, image-to-paragraph placement) rather than pinning the specific
// content of the real captured demo run in src/test/fixtures.ts — that fixture is free
// to change without these tests needing to track its prose. Each test serves its own
// small, purpose-built RunResultView through MSW instead.
function baseRun(overrides: Partial<NonNullable<RunResultView['run']>> = {}): RunResultView {
  return {
    id: 'run-article-test',
    status: 'succeeded',
    error: null,
    run: {
      id: 'run-article-test',
      brief: {
        destination: 'Belém',
        start_date: '2026-09-14',
        end_date: '2026-09-16',
        pace: 'balanced',
        language: 'en',
      },
      status: 'succeeded',
      ...overrides,
    },
  }
}

function renderArticle(runId: string) {
  return renderRoutes([{ path: '/runs/:runId/article', element: <ArticlePage /> }], {
    initialEntries: [`/runs/${runId}/article`],
  })
}

function serve(result: RunResultView) {
  server.use(http.get('/api/runs/:runId/result', () => HttpResponse.json(result)))
}

describe('ArticlePage', () => {
  test('renders Editor\'s revised body, not Chronicler\'s pre-edit draft', async () => {
    serve(
      baseRun({
        content: {
          itinerary_id: 'itin-1',
          edited: false,
          pieces: [
            {
              id: 'piece-article',
              kind: 'article',
              title: 'Belém, on foot and on the record',
              body: 'Belém in September is a slow, golden-lit stretch of riverside worth savouring. Begin at 10:00.',
            },
          ],
        },
        edited: {
          itinerary_id: 'itin-1',
          edited: true,
          pieces: [
            {
              id: 'piece-article',
              kind: 'article',
              title: 'Belém, on foot and on the record',
              body: 'Begin at 10:00. The Mosteiro dos Jerónimos anchors the morning.',
            },
          ],
        },
      }),
    )
    renderArticle('run-article-test')

    expect(await screen.findByRole('heading', { name: 'Belém, on foot and on the record' })).toBeInTheDocument()
    // The pre-edit article opened with a scene-setting sentence Editor cut for stating
    // no price, route, or duration — it should not be on the page.
    expect(screen.queryByText(/slow, golden-lit stretch/i)).not.toBeInTheDocument()
    expect(screen.getByText(/Begin at 10:00\. The Mosteiro dos Jerónimos anchors the morning\./)).toBeInTheDocument()
  })

  test('shows both Editor and Illustrator caveats under one heading', async () => {
    serve(
      baseRun({
        edited: {
          itinerary_id: 'itin-1',
          edited: true,
          pieces: [{ id: 'piece-article', kind: 'article', title: 'Belém', body: 'Begin at 10:00.' }],
          caveats: ["Cut a sentence of pure atmosphere from the 'article' piece: no price, route, or duration behind it."],
        },
        illustrated: {
          itinerary_id: 'itin-1',
          edited: true,
          pieces: [{ id: 'piece-article', kind: 'article', title: 'Belém', body: 'Begin at 10:00.' }],
          images: [],
          caveats: ['The contradicted ticket-price claim was not stated as fact.'],
        },
      }),
    )
    renderArticle('run-article-test')

    expect(await screen.findByRole('heading', { name: 'Editing and illustration notes' })).toBeInTheDocument()
    expect(screen.getByText(/Cut a sentence of pure atmosphere/i)).toBeInTheDocument()
    expect(screen.getByText(/contradicted ticket-price claim/i)).toBeInTheDocument()
  })

  test('places an image next to the paragraph that names its subject, not a fixed position', async () => {
    serve(
      baseRun({
        edited: {
          itinerary_id: 'itin-1',
          edited: true,
          pieces: [
            {
              id: 'piece-article',
              kind: 'article',
              title: 'Belém, on foot and on the record',
              body: 'Begin in Belém at 10:00.\n\nThe Mosteiro dos Jerónimos anchors the morning.',
              claim_ids: ['claim-hours'],
            },
          ],
        },
        illustrated: {
          itinerary_id: 'itin-1',
          edited: true,
          pieces: [
            {
              id: 'piece-article',
              kind: 'article',
              title: 'Belém, on foot and on the record',
              body: 'Begin in Belém at 10:00.\n\nThe Mosteiro dos Jerónimos anchors the morning.',
              claim_ids: ['claim-hours'],
            },
          ],
          images: [
            {
              id: 'image-jeronimos',
              subject: 'Mosteiro dos Jerónimos',
              claim_ids: ['claim-hours'],
              prompt: 'Editorial travel photograph of Mosteiro dos Jerónimos.',
              data_base64: 'aGVsbG8=',
              mime_type: 'image/png',
            },
          ],
        },
      }),
    )
    renderArticle('run-article-test')

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
    serve(
      baseRun({
        content: {
          itinerary_id: 'itin-1',
          edited: false,
          pieces: [
            {
              id: 'piece-article',
              kind: 'article',
              title: 'Belém, on foot and on the record',
              body: 'Belém in September is a slow, golden-lit stretch of riverside worth savouring.',
            },
          ],
        },
        edited: null,
      }),
    )
    renderArticle('run-article-test')

    expect(await screen.findByRole('heading', { name: 'Belém, on foot and on the record' })).toBeInTheDocument()
    // Falls all the way back to the pre-edit sentence Editor would otherwise have cut.
    expect(screen.getByText(/slow, golden-lit stretch/i)).toBeInTheDocument()
  })
})
