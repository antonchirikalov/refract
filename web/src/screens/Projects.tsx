import { useEffect, useState } from 'react'

import { api } from '../api'
import { href } from '../route'
import type { RunSummary } from '../types'

interface Card {
  id: string
  pipelines: string[]
  lastRun: RunSummary | null
}

export function Projects() {
  const [cards, setCards] = useState<Card[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const ids = await api.projects()
        const rows = await Promise.all(
          ids.map(async (id) => {
            const [pipelines, runs] = await Promise.all([
              api.pipelines(id).catch(() => []),
              api.runs(id).catch(() => [] as RunSummary[]),
            ])
            return { id, pipelines, lastRun: runs[0] ?? null }
          }),
        )
        if (alive) setCards(rows)
      } catch (e) {
        if (alive) setError(String(e))
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!cards) return <p className="muted">loading…</p>

  return (
    <section>
      <header className="screen-head">
        <h1>Projects</h1>
        <a className="button" href={href({ screen: 'new-project' })}>
          New project
        </a>
      </header>

      {cards.length === 0 ? (
        <p className="muted">
          No projects yet. Create one from a template to get started.
        </p>
      ) : (
        <ul className="cards">
          {cards.map((card) => (
            <li key={card.id} className="card">
              <a href={href({ screen: 'project', project: card.id })}>
                <h2>{card.id}</h2>
                <p className="muted">
                  {card.pipelines.join(', ') || 'no pipeline yet'}
                </p>
                {card.lastRun ? (
                  <p className={`status is-${card.lastRun.status}`}>
                    last run {card.lastRun.status} · {card.lastRun.created_at}
                  </p>
                ) : (
                  <p className="muted">never run</p>
                )}
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
