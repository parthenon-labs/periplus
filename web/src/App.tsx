import { Navigate, Route, Routes } from 'react-router-dom'
import { RunProgressPage } from './routes/RunProgressPage'
import { RunResultPage } from './routes/RunResultPage'
import { TripInputPage } from './routes/TripInputPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<TripInputPage />} />
      <Route path="/runs/:runId" element={<RunProgressPage />} />
      <Route path="/runs/:runId/result" element={<RunResultPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
