import { useEffect, useState } from 'react'

export default function App() {
  const [attempt, setAttempt] = useState(0)
  const [statuses, setStatuses] = useState(['Checking…', 'Checking…'])

  useEffect(() => {
    const controller = new AbortController()
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(5000)])
    Promise.all(['/api/health', '/api/ready'].map(async (path) => {
      try {
        const response = await fetch(path, { signal })
        const body = await response.json()
        return response.ok ? body.status : 'Unavailable'
      } catch {
        return 'Unavailable'
      }
    })).then((results) => {
      if (!controller.signal.aborted) setStatuses(results)
    })
    return () => controller.abort()
  }, [attempt])

  return (
    <main>
      <header>
        <p className="eyebrow">Local development · Iteration 01</p>
        <h1>Factory Intelligence</h1>
        <p>The foundation for our simulated factory.</p>
      </header>
      <section aria-labelledby="environment-title">
        <h2 id="environment-title">Environment</h2>
        <dl aria-live="polite">
          {['Application API', 'PostgreSQL'].map((name, index) => (
            <div key={name}><dt>{name}</dt><dd>{statuses[index]}</dd></div>
          ))}
        </dl>
        <button type="button" onClick={() => {
          setStatuses(['Checking…', 'Checking…'])
          setAttempt((value) => value + 1)
        }}>Check connection</button>
        <a href="http://127.0.0.1:8000/docs">API reference</a>
      </section>
      <p className="note">The SimPy source and local event collector run from the terminal.
        Factory metrics and Isaac Sim integration are the next development steps.</p>
    </main>
  )
}
