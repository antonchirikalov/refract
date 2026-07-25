import { expect, test } from '@playwright/test'

import {
  createProject,
  element,
  node,
  runToCheckpoint,
  watchForBreakage,
} from './helpers'

test('a run parks at its checkpoint, then continues to completion', async ({ page }) => {
  const problems = watchForBreakage(page)
  const project = await createProject(page, 'requirements_to_design')

  await runToCheckpoint(page)

  // parked: the run says so, the reviewable output is linked, downstream is untouched
  await expect(page.locator('.pill.is-waiting_human')).toBeVisible()
  await expect(page.locator('.panel.warn a', { hasText: 'doc.md' })).toBeVisible()
  await expect(
    node(page, 'design').locator('.gnode-status'),
  ).toHaveText('pending')

  // the parked run is visible from the project, not only from inside the run
  await page.locator('.screen-head a.back').click()
  await expect(page.locator('.panel.warn')).toContainText('A run is waiting for you')
  await page.getByRole('link', { name: 'Continue run' }).click()

  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.locator('.pill.is-completed')).toBeVisible({ timeout: 60_000 })
  await expect(
    node(page, 'sd_refine').locator('.gnode-status'),
  ).toHaveText('done')
  // every step's artifacts are reachable, including map elements and loop rounds
  await expect(page.locator('.rows a', { hasText: 'design_doc.md' }).first()).toBeVisible()
  expect(problems).toEqual([])
  expect(project).toBeTruthy()
})

test('the event feed replays the whole run log for a late viewer', async ({ page }) => {
  const problems = watchForBreakage(page)
  await createProject(page, 'requirements_to_design')
  await runToCheckpoint(page)
  const url = page.url()

  // arrive fresh at a parked run: history must be there, not just future events
  await page.goto('/#/')
  await page.goto(url)

  const feed = page.locator('.feed li')
  await expect(feed.first()).toBeVisible()
  await expect(page.locator('.feed')).toContainText('waiting_human')
  await expect(page.locator('.feed')).toContainText('scan')
  expect(problems).toEqual([])
})

test('rejecting a checkpoint stops the run instead of continuing it', async ({ page }) => {
  const problems = watchForBreakage(page)
  await createProject(page, 'requirements_to_design')
  await runToCheckpoint(page)

  await page.getByRole('button', { name: 'Stop here' }).click()

  await expect(page.locator('.pill.is-cancelled')).toBeVisible({ timeout: 60_000 })
  await expect(
    node(page, 'design').locator('.gnode-status'),
  ).toHaveText('pending')
  expect(problems).toEqual([])
})

test('a research project asks for a topic instead of documents', async ({ page }) => {
  const problems = watchForBreakage(page)

  await page.goto('/#/new')
  const name = `research-${Date.now().toString().slice(-6)}`
  await page.getByLabel('Name').fill(name)
  await page.locator('.card.selectable:has-text("research")').click()
  await expect(page.getByLabel('Topic / brief')).toBeVisible()
  await page
    .getByLabel('Topic / brief')
    .fill('How do warehouses handle offline barcode receiving?')
  await page.getByRole('button', { name: 'Create project' }).click()
  await page.waitForURL(new RegExp(`#/projects/${name}`))

  await expect(page.locator('.inline li', { hasText: 'brief.md' })).toBeVisible()
  // the discover node is the source here, and it fans out into the map
  await expect(node(page, 'find')).toContainText('discover')
  await page.getByRole('button', { name: 'Run' }).click()
  await page.waitForURL(/runs\//)
  await expect(page.locator('.pill.is-completed')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.chip-fan').first()).toContainText('map 3/3')
  expect(problems).toEqual([])
})
