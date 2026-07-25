// Hash routing, hand-rolled: four screens do not justify a router dependency.

import { useEffect, useState } from 'react'

export type Route =
  | { screen: 'projects' }
  | { screen: 'new-project' }
  | { screen: 'templates' }
  | { screen: 'project'; project: string }
  | { screen: 'run'; project: string; runId: string }

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'new') return { screen: 'new-project' }
  if (parts[0] === 'templates') return { screen: 'templates' }
  if (parts[0] === 'projects' && parts[1]) {
    if (parts[2] === 'runs' && parts[3]) {
      return { screen: 'run', project: parts[1], runId: parts[3] }
    }
    return { screen: 'project', project: parts[1] }
  }
  return { screen: 'projects' }
}

export function href(route: Route): string {
  switch (route.screen) {
    case 'new-project':
      return '#/new'
    case 'templates':
      return '#/templates'
    case 'project':
      return `#/projects/${route.project}`
    case 'run':
      return `#/projects/${route.project}/runs/${route.runId}`
    default:
      return '#/'
  }
}

export function navigate(route: Route): void {
  location.hash = href(route)
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
