import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-slate-50 px-6 text-center">
      <p className="text-xs font-bold text-blue-600 uppercase tracking-widest">Error 404</p>
      <h1 className="mt-3 text-3xl font-extrabold text-slate-900">This page could not be found</h1>
      <p className="mt-3 text-sm text-slate-600">
        The page may have been moved, or the link may be incorrect.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex items-center rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-bold text-white shadow-md hover:bg-blue-700 transition-colors"
      >
        Back to home
      </Link>
    </main>
  );
}
