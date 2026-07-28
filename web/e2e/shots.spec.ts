import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'

import { createProject, element, node, runToCheckpoint, watchForBreakage } from './helpers'

/**
 * Captures the screenshots README uses. It is a real spec, not a side script: it runs
 * against the built app and the real engine, so a screenshot can never show a UI that
 * no longer works. Regenerate with `npx playwright test shots`.
 */

const SHOTS = resolve(process.cwd(), '..', 'docs', 'architecture')

test.beforeAll(() => {
  mkdirSync(SHOTS, { recursive: true })
})

// tall enough that the whole graph fits without scrolling — a cropped first node
// looks like a rendering bug in a README
test.use({ viewport: { width: 1500, height: 1700 } })

test('screenshots: pipeline graph, live run, checkpoint, inspector', async ({ page }) => {
  const problems = watchForBreakage(page)
  const project = await createProject(page, 'requirements_to_design', 'demo-rfp')

  // 1. the pipeline as the UI derives it: containers with their elements, model badges
  await expect(node(page, 'choose')).toBeVisible()
  await page.locator('.graph-stage').screenshot({ path: `${SHOTS}/ui-graph.png` })

  // 2. one element selected: the inspector beside the graph
  await element(page, 'requirements_critic').click()
  await expect(page.locator('.inspector')).toBeVisible()
  await page.locator('.workbench').screenshot({ path: `${SHOTS}/ui-inspector.png` })

  // 3. a run parked at its checkpoint, with the produced artifacts linked
  await page.locator('.gnode').first().click({ force: true })  // clear the selection
  await runToCheckpoint(page)
  await expect(page.locator('.panel.warn')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/ui-run-checkpoint.png`, fullPage: false })

  expect(problems).toEqual([])
})
