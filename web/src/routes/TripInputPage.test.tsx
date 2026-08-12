import { screen } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { renderRoutes } from '../test/renderWithProviders'
import { server } from '../test/mocks/server'
import { TripInputPage } from './TripInputPage'

function renderTripInput() {
  return renderRoutes([
    { path: '/', element: <TripInputPage /> },
    { path: '/runs/:runId', element: <div>run progress page</div> },
  ])
}

// The date fields are a custom calendar popover, not a native `<input
// type="date">`: open it, page to the target month, then click the day cell
// (identified by its full-date accessible name, same as a screen reader
// would announce it).
async function pickDate(user: UserEvent, triggerName: string, iso: string) {
  await user.click(screen.getByRole('button', { name: triggerName }))
  const target = new Date(`${iso}T00:00:00`)
  const now = new Date()
  const monthsForward = (target.getFullYear() - now.getFullYear()) * 12 + (target.getMonth() - now.getMonth())
  const nextMonth = screen.getByRole('button', { name: 'Next month' })
  for (let i = 0; i < monthsForward; i++) {
    await user.click(nextMonth)
  }
  const dayLabel = new Intl.DateTimeFormat('en', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(target)
  await user.click(screen.getByRole('button', { name: dayLabel }))
}

describe('TripInputPage validation', () => {
  test('blocks submission and shows an error when destination is empty', async () => {
    const user = userEvent.setup()
    renderTripInput()

    await user.click(screen.getByRole('button', { name: /chart this trip/i }))

    expect(await screen.findByText('Enter a destination.')).toBeInTheDocument()
    expect(screen.queryByText('run progress page')).not.toBeInTheDocument()
  })

  test('blocks submission when the return date is before the departure date', async () => {
    const user = userEvent.setup()
    renderTripInput()

    await user.type(screen.getByPlaceholderText('Where to?'), 'Lisbon')
    await pickDate(user, 'Depart', '2026-09-14')
    await pickDate(user, 'Return', '2026-09-01')
    await user.click(screen.getByRole('button', { name: /chart this trip/i }))

    expect(await screen.findByText('End date must be on or after the start date.')).toBeInTheDocument()
  })

  test('shows a nights count once both dates are valid', async () => {
    const user = userEvent.setup()
    renderTripInput()

    await pickDate(user, 'Depart', '2026-09-14')
    await pickDate(user, 'Return', '2026-09-16')

    expect(await screen.findByText('2 nights')).toBeInTheDocument()
  })

  test('submits a valid brief and navigates to the run progress page', async () => {
    const user = userEvent.setup()
    renderTripInput()

    await user.type(screen.getByPlaceholderText('Where to?'), 'Lisbon')
    await pickDate(user, 'Depart', '2026-09-14')
    await pickDate(user, 'Return', '2026-09-16')
    expect(screen.getByDisplayValue('Lisbon')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /chart this trip/i }))

    expect(await screen.findByText('run progress page')).toBeInTheDocument()
  })

  test('maps a server-side 422 validation issue back onto the matching field', async () => {
    server.use(
      http.post('/api/trips', () =>
        HttpResponse.json(
          {
            detail: [
              {
                loc: ['body', 'destination'],
                msg: 'Destination is not recognised.',
                type: 'value_error',
                input: 'Nowhereville',
              },
            ],
          },
          { status: 422 },
        ),
      ),
    )

    const user = userEvent.setup()
    renderTripInput()

    await user.type(screen.getByPlaceholderText('Where to?'), 'Nowhereville')
    await pickDate(user, 'Depart', '2026-09-14')
    await pickDate(user, 'Return', '2026-09-16')
    await user.click(screen.getByRole('button', { name: /chart this trip/i }))

    expect(await screen.findByText('Destination is not recognised.')).toBeInTheDocument()
    expect(screen.queryByText('run progress page')).not.toBeInTheDocument()
  })
})
