import { useEffect, useState } from 'react'

import { api } from '../api'
import { href } from '../route'
import type { TemplateInfo } from '../types'

/** The template gallery: what a new project can start from (SPEC-UI §4). */
export function Templates() {
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void api.templates().then(setTemplates).catch((e) => setError(String(e)))
  }, [])

  if (error) return <pre className="error">{error}</pre>
  if (!templates) return <p className="muted">loading…</p>

  return (
    <section>
      <header className="screen-head">
        <h1>Templates</h1>
        <a className="button" href={href({ screen: 'new-project' })}>
          New project
        </a>
      </header>

      <ul className="cards">
        {templates.map((t) => (
          <li key={t.name} className="card">
            <div className="template-body">
            <h2>
              {t.title}{' '}
              {t.source === 'user' ? <span className="tag">yours</span> : null}
            </h2>
            <p>{t.description || <span className="muted">no description</span>}</p>
            <p className="meta">
              {t.input_mode === 'brief' ? 'starts from a topic' : 'starts from documents'}
            </p>
            <ol className="chain">
              {t.nodes.map((n) => (
                <li key={n.id}>
                  {n.id}
                  <span className="muted">
                    {n.type.startsWith('builtin/') ? n.type.slice(8) : n.type}
                  </span>
                </li>
              ))}
            </ol>
            {(t.checkpoints ?? []).length ? (
              <p className="meta">stops for review after: {(t.checkpoints ?? []).join(', ')}</p>
            ) : null}
            <p className="meta">agents: {t.agents.join(', ')}</p>
            {t.needs.length ? (
              <p className="meta">capabilities: {t.needs.join(', ')}</p>
            ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
