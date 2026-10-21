import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

function ownerSecretFromEnv(): string {
  const envText = readFileSync(resolve(process.cwd(), '../../.env'), 'utf8');
  const line = envText.split(/\r?\n/).find((entry) => /^\s*AUTH_OWNER_SECRET\s*=/.test(entry));
  const value = line?.replace(/^\s*AUTH_OWNER_SECRET\s*=\s*/, '').trim();
  const secret = value?.replace(/^(?:"(.*)"|'(.*)')$/, (_match, doubleQuoted: string | undefined, singleQuoted: string | undefined) => doubleQuoted ?? singleQuoted ?? '');
  if (!secret) throw new Error('AUTH_OWNER_SECRET is missing from placidway-assesment/.env.');
  return secret;
}

test('live owner chat returns sourced Arabic cost guidance and restores from history', async ({ page }) => {
  const ownerSecret = ownerSecretFromEnv();
  let knowledgeStatus: { ready: boolean; source_count: number; price_count: number } | undefined;
  page.on('response', async (response) => {
    if (new URL(response.url()).pathname === '/api/knowledge/status' && response.request().method() === 'GET') {
      try {
        knowledgeStatus = await response.json() as typeof knowledgeStatus;
      } catch {
        // The assertion below reports missing or invalid live status without exposing response bodies.
      }
    }
  });

  await page.goto('/');
  await page.getByLabel('Access secret').fill(ownerSecret);
  await page.getByRole('button', { name: 'Continue securely' }).click();
  await expect(page.getByRole('button', { name: 'Refresh knowledge sources' })).toBeVisible();
  await expect.poll(() => knowledgeStatus?.ready, { timeout: 15_000 }).toBe(true);
  expect(knowledgeStatus?.source_count).toBe(7);
  expect(knowledgeStatus?.price_count).toBe(59);
  await expect(page.getByText('Sources up to date')).toBeVisible();

  await expect(page.getByRole('combobox', { name: 'Response language' })).toHaveCount(0);
  const question = 'كم تبلغ تكلفة برنامج علاج السرطان الغدي (adenocarcinoma) للمرضى الخارجيين في المكسيك؟';
  await page.getByRole('textbox', { name: 'Ask a question' }).fill(question);
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect(page.locator('.run-status')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.run-status')).toHaveCount(0, { timeout: 120_000 });
  const answer = page.locator('.assistant-bubble[dir="rtl"]');
  await expect(answer.last()).toBeVisible();
  await expect(answer.last()).not.toBeEmpty();
  await expect(answer.last()).toContainText(/18[,٫]?995|30[,٫]?000|١٨[٫٬]?٩٩٥|٣٠[٫٬]?٠٠٠/);
  await expect(page.locator('.source-list').last()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.source-card').first()).toHaveAttribute('rel', 'noopener noreferrer');

  const activity = page.getByRole('button', { name: /How this answer came together/ });
  await expect(activity).toHaveAttribute('aria-expanded', 'false');
  await activity.click();
  const activityPanelId = await activity.getAttribute('aria-controls');
  expect(activityPanelId).toBeTruthy();
  const activityPanel = page.locator('.activity-panel');
  await expect(activityPanel).toHaveAttribute('id', activityPanelId!);
  await expect(activityPanel.locator('.activity-step').first()).toBeVisible();
  await activity.click();

  // Reload the authenticated app and reopen the newest real conversation from server history.
  await page.reload();
  await expect.poll(() => page.locator('.history-item').count(), { timeout: 15_000 }).toBeGreaterThan(0);
  await page.locator('.history-item').first().click();
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  await expect(page.locator('.assistant-bubble[dir="rtl"]').last()).toBeVisible();
  await expect(page.locator('.source-list').last()).toBeVisible();
  await expect(page.getByRole('button', { name: /How this answer came together/ })).toHaveAttribute('aria-expanded', 'false');

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.sidebar')).not.toHaveClass(/sidebar-open/);
  await expect(page.locator('.sidebar-scrim')).toHaveCount(0);
  await expect(page.locator('.sidebar')).toHaveCSS('visibility', 'hidden');
  const mobileLayout = await page.evaluate(() => ({
    mainWidth: document.querySelector('.main-panel')?.getBoundingClientRect().width,
    hamburgerLeft: document.querySelector('.menu-button')?.getBoundingClientRect().left,
  }));
  expect(mobileLayout.mainWidth).toBe(390);
  expect(mobileLayout.hamburgerLeft).toBeGreaterThanOrEqual(16);
  const sidebar = page.locator('.sidebar');
  await expect.poll(
    () => sidebar.evaluate((element) => Math.round(element.getBoundingClientRect().right)),
    { timeout: 2_000 },
  ).toBeLessThanOrEqual(0);
  await expect(sidebar).toHaveCSS('visibility', 'hidden');
  await expect(sidebar).not.toBeInViewport();
  await page.screenshot({ path: 'test-results/live-mobile.png' });
});
