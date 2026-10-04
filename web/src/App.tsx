import { BrowserRouter, Route, Routes } from 'react-router'

// Placeholder shell — real pages land in step 2.
function Home() {
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">Unobio</h1>
      <p className="mt-2 text-slate-600">AI biotech investment underwriting.</p>
    </main>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
      </Routes>
    </BrowserRouter>
  )
}
