import { BrowserRouter, Route, Routes } from 'react-router'
import AppShell from './components/AppShell'
import NewAnalysis from './pages/NewAnalysis'
import ReportPage from './pages/Report'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<NewAnalysis />} />
          <Route path="/runs/:id" element={<ReportPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
