import type { Variants } from 'framer-motion'

/** Parent variant: staggers children that use `rise`. */
export const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
}

/** Child variant: fade + small upward slide. */
export const rise: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' } },
}
