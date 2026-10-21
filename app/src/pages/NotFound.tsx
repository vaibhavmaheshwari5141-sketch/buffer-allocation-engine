import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <section className="py-20 text-center">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <Link to="/" className="mt-4 inline-block text-brand-600 hover:underline dark:text-brand-300">
        Back to home
      </Link>
    </section>
  )
}
