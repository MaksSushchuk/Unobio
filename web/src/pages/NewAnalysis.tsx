import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, ShieldCheck, Scale, type LucideIcon } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import PipelineRun from '../components/pipeline/PipelineRun'
import { rise, stagger } from '../lib/motion'
import { RESULT_RUN_ID } from '../pipeline.config'
import type { ReportInput } from '../types'

// Form state keeps biomarkers as the raw comma-separated string; it is split
// into string[] only when building the ReportInput.
interface FormState {
  indication: string
  mechanism: string
  modality: string
  stage: string
  biomarkers: string
  route: string
}

const EMPTY_FORM: FormState = {
  indication: '',
  mechanism: '',
  modality: '',
  stage: '',
  biomarkers: '',
  route: '',
}

const PRESETS: { label: string; tag: string; icon: LucideIcon; form: FormState }[] = [
  {
    label: "IL-17 + Crohn's disease",
    tag: 'Demo: contradictory evidence',
    icon: Scale,
    form: {
      indication: "Crohn's disease",
      mechanism: 'IL-17 inhibition',
      modality: 'monoclonal antibody',
      stage: 'Phase 1 complete (healthy volunteers)',
      biomarkers: 'fecal calprotectin, CRP, mucosal IL17A expression',
      route: 'subcutaneous',
    },
  },
  {
    label: 'PCSK9 + hypercholesterolemia',
    tag: 'Validated mechanism',
    icon: ShieldCheck,
    form: {
      indication: 'Hypercholesterolemia',
      mechanism: 'PCSK9 inhibition',
      modality: 'small interfering RNA',
      stage: 'Phase 2',
      biomarkers: 'LDL-C, Lp(a), PCSK9 plasma levels',
      route: 'subcutaneous',
    },
  },
]

// Order fields flash in after a preset is applied.
const FIELD_ORDER: (keyof FormState)[] = ['indication', 'mechanism', 'modality', 'stage', 'biomarkers', 'route']

function toReportInput(form: FormState): ReportInput {
  const input: ReportInput = {
    indication: form.indication.trim(),
    mechanism: form.mechanism.trim(),
  }
  if (form.modality.trim()) input.modality = form.modality.trim()
  if (form.stage.trim()) input.stage = form.stage.trim()
  if (form.route.trim()) input.route = form.route.trim()
  const biomarkers = form.biomarkers
    .split(',')
    .map((b) => b.trim())
    .filter(Boolean)
  if (biomarkers.length) input.biomarkers = biomarkers
  return input
}

export default function NewAnalysis() {
  const navigate = useNavigate()
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [showOptional, setShowOptional] = useState(false)
  // Bumped on each preset click; keys the highlight animation on filled fields.
  const [flash, setFlash] = useState(0)
  const [runInput, setRunInput] = useState<ReportInput | null>(null)

  const canSubmit = form.indication.trim() !== '' && form.mechanism.trim() !== ''

  function update(field: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [field]: value }))
  }

  function applyPreset(preset: FormState) {
    setForm(preset)
    setShowOptional(true)
    setFlash((n) => n + 1)
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (canSubmit) setRunInput(toReportInput(form))
  }

  const fieldProps = (field: keyof FormState) => ({
    value: form[field],
    onChange: (v: string) => update(field, v),
    flash: flash && form[field] ? flash : 0,
    flashDelay: FIELD_ORDER.indexOf(field) * 0.05,
  })

  return (
    <div className="relative isolate">
      <HeaderBackdrop />
      <AnimatePresence mode="wait">
        {runInput ? (
          <motion.div
            key="run"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
          >
            <PipelineRun
              input={runInput}
              onComplete={() => navigate(`/runs/${RESULT_RUN_ID}`)}
              onCancel={() => setRunInput(null)}
            />
          </motion.div>
        ) : (
          <motion.div
            key="form"
            variants={stagger}
            initial="hidden"
            animate="show"
            exit={{ opacity: 0, y: -8, transition: { duration: 0.2 } }}
            className="space-y-6"
          >
            <motion.div variants={rise}>
              <h1 className="text-2xl font-semibold tracking-tight">New analysis</h1>
              <p className="mt-1 text-sm text-slate-500">
                Underwrite a therapeutic thesis from an indication and mechanism of action.
              </p>
            </motion.div>

            <motion.div variants={rise}>
              <h2 className="mb-2 text-xs font-medium tracking-wide text-slate-500 uppercase">Start from a preset</h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {PRESETS.map((p) => (
                  <PresetCard key={p.label} {...p} onClick={() => applyPreset(p.form)} />
                ))}
              </div>
            </motion.div>

            <motion.form
              variants={rise}
              onSubmit={onSubmit}
              className="rounded-xl border border-slate-200 bg-white shadow-sm"
            >
              <div className="space-y-5 px-6 py-6">
                <div className="grid gap-5 sm:grid-cols-2">
                  <Field label="Indication" required placeholder="e.g. Crohn's disease" {...fieldProps('indication')} />
                  <Field label="Mechanism" required placeholder="e.g. IL-17 inhibition" {...fieldProps('mechanism')} />
                </div>

                <div>
                  <button
                    type="button"
                    onClick={() => setShowOptional((s) => !s)}
                    aria-expanded={showOptional}
                    className="flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900"
                  >
                    <motion.span animate={{ rotate: showOptional ? 90 : 0 }} transition={{ duration: 0.2 }} className="flex">
                      <ChevronRight className="h-4 w-4" />
                    </motion.span>
                    Optional details
                  </button>
                  <AnimatePresence initial={false}>
                    {showOptional && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.25, ease: 'easeOut' }}
                        className="-mx-1 overflow-hidden px-1"
                      >
                        <div className="grid gap-5 pt-4 pb-1 sm:grid-cols-2">
                          <Field label="Modality" placeholder="e.g. monoclonal antibody" {...fieldProps('modality')} />
                          <Field label="Stage" placeholder="e.g. Phase 1" {...fieldProps('stage')} />
                          <Field
                            label="Biomarkers"
                            hint="Comma-separated"
                            placeholder="e.g. CRP, fecal calprotectin"
                            {...fieldProps('biomarkers')}
                          />
                          <Field label="Route" placeholder="e.g. subcutaneous" {...fieldProps('route')} />
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 rounded-b-xl border-t border-slate-100 bg-slate-50/60 px-6 py-4">
                {!canSubmit && <span className="text-xs text-slate-400">Indication and mechanism are required</span>}
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  Run underwriting
                </button>
              </div>
            </motion.form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Faint grid fading out below the page header; full-bleed behind the content. */
function HeaderBackdrop() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute -top-10 left-1/2 -z-10 h-80 w-screen -translate-x-1/2"
      style={{
        backgroundImage:
          'linear-gradient(to right, rgb(148 163 184 / 0.14) 1px, transparent 1px), linear-gradient(to bottom, rgb(148 163 184 / 0.14) 1px, transparent 1px), radial-gradient(ellipse 60% 80% at 50% 0%, rgb(226 232 240 / 0.7), transparent)',
        backgroundSize: '32px 32px, 32px 32px, 100% 100%',
        maskImage: 'linear-gradient(to bottom, black 20%, transparent)',
      }}
    />
  )
}

function PresetCard(props: { label: string; tag: string; icon: LucideIcon; onClick: () => void }) {
  const Icon = props.icon
  return (
    <motion.button
      type="button"
      onClick={props.onClick}
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.99 }}
      className="group flex items-center gap-3 rounded-lg border border-slate-200 bg-white/90 px-4 py-3 text-left shadow-xs transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-sm focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:outline-none"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-600 transition-colors group-hover:bg-slate-900 group-hover:text-white">
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-slate-900">{props.label}</span>
        <span className="block truncate text-xs text-slate-500">{props.tag}</span>
      </span>
    </motion.button>
  )
}

function Field(props: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  required?: boolean
  hint?: string
  /** Non-zero to play the "just filled" highlight; a new value replays it. */
  flash: number
  flashDelay: number
}) {
  return (
    <label className="block">
      <span className="flex items-baseline gap-2 text-sm font-medium text-slate-700">
        {props.label}
        {props.required && <span className="text-slate-400">*</span>}
        {props.hint && <span className="text-xs font-normal text-slate-400">{props.hint}</span>}
      </span>
      <span className="relative mt-1.5 block rounded-md bg-white">
        {props.flash > 0 && (
          <motion.span
            key={props.flash}
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-md bg-sky-100/70 ring-1 ring-sky-300"
            initial={{ opacity: 1 }}
            animate={{ opacity: 0 }}
            transition={{ duration: 1.1, delay: 0.15 + props.flashDelay, ease: 'easeOut' }}
          />
        )}
        <input
          type="text"
          required={props.required}
          value={props.value}
          placeholder={props.placeholder}
          onChange={(e) => props.onChange(e.target.value)}
          className="relative block w-full rounded-md border border-slate-300 bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-slate-400 focus:border-slate-500 focus:ring-2 focus:ring-slate-200 focus:outline-none"
        />
      </span>
    </label>
  )
}
