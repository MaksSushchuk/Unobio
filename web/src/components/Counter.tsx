import { motion, useReducedMotion, useSpring, useTransform } from 'framer-motion'
import { useEffect } from 'react'

/** A number that springs up to `value`; renders the value directly under reduced motion. */
export default function Counter({ value, format }: { value: number; format: (n: number) => string }) {
  const reduce = useReducedMotion()
  const spring = useSpring(0, { stiffness: 90, damping: 20 })
  const text = useTransform(spring, format)

  useEffect(() => {
    spring.set(value)
  }, [spring, value])

  if (reduce) return <span>{format(value)}</span>
  return <motion.span>{text}</motion.span>
}
