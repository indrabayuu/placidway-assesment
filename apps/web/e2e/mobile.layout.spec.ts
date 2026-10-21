import { expect, test } from '@playwright/test';

test('closed mobile navigation leaves the full viewport for chat', async ({ page }) => {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Workspace owner' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route('**/api/knowledge/status', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ready: true, version: 'mobile-test', updated_at: null, source_count: 7, chunk_count: 54, price_count: 54, refresh_running: false, last_error: null }) });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Refresh knowledge sources' })).toBeVisible();

  await expect(page.locator('.sidebar')).not.toHaveClass(/sidebar-open/);
  await expect(page.locator('.sidebar')).toHaveCSS('visibility', 'hidden');
  await expect(page.locator('.sidebar-scrim')).toHaveCount(0);
  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    mainWidth: document.querySelector('.main-panel')?.getBoundingClientRect().width,
    hamburgerLeft: document.querySelector('.menu-button')?.getBoundingClientRect().left,
  }));
  expect(layout.mainWidth).toBe(layout.viewportWidth);
  expect(layout.hamburgerLeft).toBeGreaterThanOrEqual(16);
  const menuButton = page.getByRole('button', { name: 'Open navigation' });
  await expect(menuButton).toHaveAttribute('aria-expanded', 'false');
  await menuButton.click();
  await expect(page.locator('#conversation-sidebar')).toHaveAttribute('role', 'dialog');
  const closeButton = page.locator('.mobile-close');
  await expect(closeButton).toBeFocused();
  await closeButton.press('Shift+Tab');
  await expect(page.locator('.logout-button')).toBeFocused();
  await page.keyboard.press('Escape');
  const sidebar = page.locator('.sidebar');
  await expect(sidebar).toHaveCSS('visibility', 'hidden');
  await expect(menuButton).toBeFocused();
  await expect.poll(
    () => sidebar.evaluate((element) => Math.round(element.getBoundingClientRect().right)),
    { timeout: 2_000 },
  ).toBeLessThanOrEqual(0);
  await expect(sidebar).not.toBeInViewport();
  await page.screenshot({ path: 'test-results/live-mobile.png', animations: 'disabled' });
});
