import { motion } from 'framer-motion'

/** 0–1 confidence as a horizontal bar. Grows via scaleX, so reduced motion shows it at full width immediately. */
export default function ConfidenceBar(props: {
  value: number
  label?: string
  size?: 'sm' | 'md'
  /** Tailwind bg class for the filled part. */
  tone?: string
  delay?: number
}) {
  const { value, label = 'Confidence', size = 'sm', tone = 'bg-slate-700', delay = 0 } = props
  const pct = Math.round(value * 100)
  return (
    <div className="min-w-0">
      <div className={`flex items-baseline justify-between gap-2 ${size === 'md' ? 'text-sm' : 'text-xs'}`}>
        <span className="text-slate-500">{label}</span>
        <span className="font-medium text-slate-900 tabular-nums">{pct}%</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className={`mt-1 overflow-hidden rounded-full bg-slate-100 ${size === 'md' ? 'h-2' : 'h-1.5'}`}
      >
        <motion.div
          className={`h-full origin-left rounded-full ${tone}`}
          style={{ width: `${pct}%` }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: 0.7, delay, ease: 'easeOut' }}
        />
      </div>
    </div>
  )
}
