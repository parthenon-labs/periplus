import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, HashRouter } from 'react-router-dom'
import App from './App.tsx'
import { queryClient } from './app/queryClient.ts'
import './index.css'

// Dev-only mock preview: append "?mock=1" while running `npm run dev` to
// drive the UI from the existing MSW fixtures instead of a live backend.
// import.meta.env.DEV is statically replaced by Vite, so this branch (and
// the msw/browser import) is dead-code-eliminated from production builds.
async function enableMockingIfRequested() {
  if (!import.meta.env.DEV) return
  if (new URLSearchParams(window.location.search).get('mock') !== '1') return
  const { worker } = await import('./mocks/browser')
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true })
}

const Router = import.meta.env.MODE === 'demo' ? HashRouter : BrowserRouter

enableMockingIfRequested().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Router>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </Router>
    </StrictMode>,
  )
})
