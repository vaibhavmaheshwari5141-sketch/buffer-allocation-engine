import { useState } from 'react'

const steps = [
  'Problem',
  'Data',
  'Lineage',
  'Notebooks',
  'Models',
  'Optimisation',
  'Results',
  'Reconciliation',
]

export default function Journey() {
  const [active, setActive] = useState(0)

  return (
    <div className="flex flex-col gap-6 md:flex-row">
      <aside className="md:w-56 md:shrink-0">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Project journey
        </h2>
        <ol className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
          {steps.map((step, i) => (
            <li key={step} className="shrink-0">
              <button
                type="button"
                onClick={() => setActive(i)}
                aria-current={active === i ? 'step' : undefined}
                className={[
                  'flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm',
                  active === i
                    ? 'bg-brand-50 font-medium text-brand-700 dark:bg-slate-800 dark:text-brand-300'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
                ].join(' ')}
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-current text-xs">
                  {i + 1}
                </span>
                {step}
              </button>
            </li>
          ))}
        </ol>
      </aside>

      <section className="min-w-0 flex-1">
        <h1 className="text-2xl font-bold tracking-tight">{steps[active]}</h1>
        <div className="mt-6 rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
          Placeholder. Content for this step comes in a later phase.
        </div>
      </section>
    </div>
  )
}
