import { useEffect, useState } from 'react'

import { api, ApiError } from '../api'
import { navigate } from '../route'
import type { ProviderInfo, TemplateInfo } from '../types'

/** New project: pick a template, then give it documents or a brief (SPEC-UI §4). */
export function NewProject() {
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [name, setName] = useState('')
  const [template, setTemplate] = useState<string | null>(null)
  const [model, setModel] = useState('')
  const [docsPath, setDocsPath] = useState('')
  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void api.templates().then(setTemplates).catch((e) => setError(String(e)))
    void api
      .models()
      .then((list) => {
        setProviders(list)
        // A project without defaults.model is born invalid (E_MODEL_UNRESOLVED): the
        // engine has no default of its own, so preselect the first usable model.
        const usable = list.find((p) => p.available && p.models.length)
        if (usable) setModel(`${usable.name}/${usable.models[0]}`)
      })
      .catch(() => setProviders([]))
  }, [])

  const chosen = templates?.find((t) => t.name === template) ?? null
  const wantsBrief = chosen?.input_mode === 'brief'
  const available = providers.filter((p) => p.available)

  async function create() {
    setBusy(true)
    setError(null)
    try {
      // Documents are COPIED into the project, so a finished run's sources cannot
      // move under it; a brief is stored as the project's single source document.
      await api.createProject({
        name: name.trim(),
        template: template ?? undefined,
        model: model || undefined,
      })
      const project = name.trim()
      if (wantsBrief) {
        if (brief.trim()) await api.putBrief(project, brief)
      } else if (docsPath.trim()) {
        await api.importDocuments(project, docsPath.trim())
      }
      navigate({ screen: 'project', project })
    } catch (e) {
      setError(
        e instanceof ApiError ? JSON.stringify(e.detail, null, 2) : String(e),
      )
    } finally {
      setBusy(false)
    }
  }

  const ready =
    name.trim().length > 0 &&
    model.length > 0 && // no model means a pipeline that cannot pass validation
    (!wantsBrief ? true : brief.trim().length > 0) &&
    !busy

  return (
    <section>
      <header className="screen-head">
        <h1>New project</h1>
      </header>

      <label className="field">
        <span>Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="warehouse-receiving"
          autoFocus
        />
      </label>

      <h2>Template</h2>
      {!templates ? (
        <p className="muted">loading…</p>
      ) : (
        <ul className="cards">
          {templates.map((t) => (
            <li
              key={t.name}
              className={`card selectable ${template === t.name ? 'is-selected' : ''}`}
            >
              <button type="button" onClick={() => setTemplate(t.name)}>
                <h3>
                  {t.title}{' '}
                  {t.source === 'user' ? <span className="tag">yours</span> : null}
                </h3>
                <p className="muted">{t.description || 'no description'}</p>
                <p className="meta">
                  {t.input_mode === 'brief' ? 'needs a topic' : 'needs documents'} ·{' '}
                  {t.nodes.length} nodes
                </p>
                {t.needs.some((n) => n.startsWith('mcp:') || n === 'bash') ? (
                  <p className="warn">uses {t.needs.join(', ')}</p>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}

      {chosen ? (
        wantsBrief ? (
          <label className="field">
            <span>Topic / brief</span>
            <textarea
              rows={6}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="What should be researched, and what would a good answer settle?"
            />
          </label>
        ) : (
          <label className="field">
            <span>Documents folder (copied into the project)</span>
            <input
              value={docsPath}
              onChange={(e) => setDocsPath(e.target.value)}
              placeholder="C:\\clients\\acme\\discovery"
            />
          </label>
        )
      ) : null}

      <label className="field">
        <span>Default model</span>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="" disabled>
            pick a model
          </option>
          {providers.flatMap((p) =>
            p.models.map((id) => (
              <option
                key={`${p.name}/${id}`}
                value={`${p.name}/${id}`}
                disabled={!p.available}
              >
                {p.name}/{id}
                {p.available ? '' : ' — no key'}
              </option>
            )),
          )}
        </select>
      </label>
      {available.length === 0 ? (
        <p className="warn">
          No provider has a key in the environment — a run would fail validation.
        </p>
      ) : null}

      {error ? <pre className="error">{error}</pre> : null}

      <button
        type="button"
        className="button primary"
        disabled={!ready}
        onClick={() => void create()}
      >
        {busy ? 'creating…' : 'Create project'}
      </button>
    </section>
  )
}
