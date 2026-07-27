import { expect, test } from '@playwright/test'

import { createProject, element, node, watchForBreakage } from './helpers'

test('the projects screen lists what is on disk', async ({ page }) => {
  const problems = watchForBreakage(page)

  await page.goto('/#/')

  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
  await expect(page.locator('.card', { hasText: 'extract-project' })).toBeVisible()
  expect(problems).toEqual([])
})

test('the template gallery explains what each template needs', async ({ page }) => {
  const problems = watchForBreakage(page)

  await page.goto('/#/templates')

  const research = page.locator('.card', { hasText: 'research' })
  await expect(research).toContainText('starts from a topic')
  const extract = page.locator('.card', { hasText: 'extract' }).first()
  await expect(extract).toContainText('starts from documents')
  // capabilities are surfaced before anything runs
  await expect(research).toContainText('tavily-remote')
  expect(problems).toEqual([])
})

test('a new project from a template shows its graph and its documents', async ({
  page,
}) => {
  const problems = watchForBreakage(page)

  const name = await createProject(page, 'requirements_to_design')

  await expect(page.getByRole('heading', { name })).toBeVisible()
  await expect(page.locator('.inline li', { hasText: 'rfp-excerpt.md' })).toBeVisible()
  // the checkpointed template says so before you run it
  await expect(page.getByText('stops for review after: refine')).toBeVisible()
  // containers render their inner agents, not just their own name
  const loop = node(page, 'refine')
  await expect(loop).toHaveClass(/is-container/)
  await expect(element(page, 'requirements_writer')).toBeVisible()
  await expect(element(page, 'requirements_critic')).toBeVisible()
  expect(problems).toEqual([])
})

test('a project with no documents cannot be run', async ({ page }) => {
  const problems = watchForBreakage(page)

  await page.goto('/#/new')
  const name = `empty-${Date.now().toString().slice(-6)}`
  await page.getByLabel('Name').fill(name)
  await page.locator('.card.selectable:has-text("extract")').first().click()
  await page.getByRole('button', { name: 'Create project' }).click()
  await page.waitForURL(new RegExp(`#/projects/${name}`))

  await expect(page.getByText('No documents yet')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run' })).toBeDisabled()
  expect(problems).toEqual([])
})

test('a project pipeline can be saved as a template and reused', async ({ page }) => {
  const problems = watchForBreakage(page)
  const name = `tpl-${Date.now().toString().slice(-7)}`
  page.on('dialog', (d) => void d.accept(name))  // the name prompt

  await page.goto('/#/projects/chain-project')
  await page.getByRole('button', { name: 'Save as template' }).click()
  await expect(page.getByRole('button', { name: 'saved as template' })).toBeVisible()

  // it is now offered to new projects, alongside the shipped ones
  await page.goto('/#/templates')
  await expect(page.locator('.card', { hasText: name })).toBeVisible()
  expect(problems).toEqual([])
})
