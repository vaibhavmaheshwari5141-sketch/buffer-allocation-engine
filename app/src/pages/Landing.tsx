import { Link } from 'react-router-dom'

export default function Landing() {
  return (
    <section className="mx-auto max-w-3xl py-12 text-center sm:py-20">
      <p className="text-sm font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-300">
        Analytics capstone
      </p>
      <h1 className="mt-3 text-3xl font-bold tracking-tight sm:text-5xl">
        Buffer Delay Allocation Engine
      </h1>
      <p className="mt-5 text-lg text-slate-600 dark:text-slate-300">
        Deciding where to place a fixed budget of ground-time buffer minutes across aircraft rotations so
        that early delays stop cascading into the rest of the day.
      </p>
      <div className="mt-10 flex flex-col justify-center gap-3 sm:flex-row">
        <Link
          to="/console"
          className="rounded-lg bg-brand-700 px-6 py-3 font-medium text-white shadow-sm hover:bg-brand-600"
        >
          Open the console
        </Link>
        <Link
          to="/journey"
          className="rounded-lg border border-slate-300 bg-white px-6 py-3 font-medium text-slate-800 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
        >
          Take the project journey
        </Link>
      </div>
    </section>
  )
}
