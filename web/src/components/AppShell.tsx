import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import { useState } from 'react'
import { Link, NavLink, useLocation, useOutlet } from 'react-router'

export default function AppShell() {
  const location = useLocation()

  return (
    // reducedMotion="user": transform/layout animations are skipped when the OS
    // asks for reduced motion; opacity fades still play.
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen overflow-x-clip bg-slate-50 text-slate-900">
        <header className="relative z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-5xl items-center gap-8 px-6">
            <Link to="/" className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-900 text-sm font-semibold text-white">
                U
              </span>
              <span className="text-[15px] font-semibold tracking-tight">Unobio</span>
            </Link>
            <nav className="flex h-full items-center">
              <NavLink
                to="/"
                end
                className={({ isActive }) =>
                  `flex h-full items-center border-b-2 px-1 text-sm font-medium transition-colors ${
                    isActive
                      ? 'border-slate-900 text-slate-900'
                      : 'border-transparent text-slate-500 hover:text-slate-900'
                  }`
                }
              >
                New analysis
              </NavLink>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.3, ease: 'easeOut' }}
            >
              <FrozenOutlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </MotionConfig>
  )
}

// Keeps rendering the route it mounted with, so the exiting page doesn't swap
// to the next route's content mid-transition.
function FrozenOutlet() {
  const outlet = useOutlet()
  const [frozen] = useState(outlet)
  return frozen
}
