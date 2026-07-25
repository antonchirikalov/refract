import { href, useRoute } from './route'
import { NewProject } from './screens/NewProject'
import { Project } from './screens/Project'
import { Projects } from './screens/Projects'
import { Run } from './screens/Run'
import { Templates } from './screens/Templates'

export default function App() {
  const route = useRoute()

  return (
    <div className="app">
      <nav>
        <a className="brand" href={href({ screen: 'projects' })}>
          refract
        </a>
        <a href={href({ screen: 'projects' })}>Projects</a>
        <a href={href({ screen: 'templates' })}>Templates</a>
      </nav>

      <main>
        {route.screen === 'projects' ? <Projects /> : null}
        {route.screen === 'new-project' ? <NewProject /> : null}
        {route.screen === 'templates' ? <Templates /> : null}
        {route.screen === 'project' ? <Project project={route.project} /> : null}
        {route.screen === 'run' ? (
          <Run project={route.project} runId={route.runId} />
        ) : null}
      </main>
    </div>
  )
}
