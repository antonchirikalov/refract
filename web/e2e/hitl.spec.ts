import { expect, test } from '@playwright/test'

import { watchForBreakage } from './helpers'

// A project whose policy requires approval for a capability parks the step BEFORE the
// agent runs (SPEC §16.10). The UI must say what is being asked and offer the decision,
// not just show a step id and a text box.
test('a capability request is explained and can be approved from the UI', async ({
  page,
}) => {
  const problems = watchForBreakage(page)

  await page.goto('/#/projects/needs-approval')
  await expect(page.locator('.gnode').first()).toBeVisible()
  await page.getByRole('button', { name: 'Run' }).click()
  await page.waitForURL(/runs\//)

  const banner = page.locator('.panel.warn')
  await expect(banner).toContainText('Permission needed before this step runs')
  // the question itself, not just a step id: this is what the banner used to omit
  await expect(banner.locator('.ask')).toContainText('Approve capabilities')
  await expect(banner.locator('.inline li', { hasText: 'webfetch' })).toBeVisible()

  await banner.getByRole('button', { name: 'Approve' }).click()

  await expect(page.locator('.pill.is-completed')).toBeVisible({ timeout: 60_000 })
  expect(problems).toEqual([])
})
