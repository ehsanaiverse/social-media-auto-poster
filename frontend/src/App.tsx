import React, { Suspense, lazy } from 'react'
import { Toaster } from 'react-hot-toast'
import './index.css'
import Header from './components/Header'
import Dashboard from './pages/Dashboard'

const Scene3D = lazy(() => import('./components/Scene3D'))

function App() {
  return (
    <div className="min-h-screen bg-grid relative text-white">
      {/* 3D background animation runs independently and never blocks the UI */}
      <Suspense fallback={null}>
        <Scene3D />
      </Suspense>

      {/* Foreground UI is always rendered on top */}
      <div className="relative z-10">
        <Header />
        <main className="container mx-auto px-4 py-8">
          <Dashboard />
        </main>
      </div>

      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: 'rgba(20,20,28,0.9)',
            color: '#fff',
            border: '1px solid rgba(255,255,255,0.1)',
            backdropFilter: 'blur(12px)',
          },
        }}
      />
    </div>
  )
}

export default App