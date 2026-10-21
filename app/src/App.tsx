import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout.tsx'
import Console from './pages/Console.tsx'
import Journey from './pages/Journey.tsx'
import Landing from './pages/Landing.tsx'
import NotFound from './pages/NotFound.tsx'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Landing />} />
        <Route path="console" element={<Console />} />
        <Route path="journey" element={<Journey />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
