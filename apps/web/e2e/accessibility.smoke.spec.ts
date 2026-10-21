import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('login and signed-in home meet WCAG 2.1 AA checks', async ({ page }) => {
  let authenticated = false;
  const now = '2026-10-21T08:00:00.000Z';
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill(authenticated
      ? { contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Workspace owner' }) }
      : { status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Sign in required.' }) });
  });
  await page.route('**/api/auth/login', async (route) => {
    authenticated = true;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Workspace owner' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill(route.request().method() === 'POST'
      ? { status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'conversation-1', title: 'New conversation', response_language: 'en', created_at: now, updated_at: now }) }
      : { contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route('**/api/knowledge/status', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ready: true, version: 'smoke', updated_at: null, source_count: 7, chunk_count: 54, price_count: 54, refresh_running: false, last_error: null }) });
  });
  await page.route('**/api/conversations/conversation-1/runs', async (route) => {
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ id: 'run-1', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-1/events' }) });
  });
  await page.route('**/api/runs/run-1/events', async (route) => {
    const events = [
      ['1', 'run.started', { run_id: 'run-1', conversation_id: 'conversation-1' }],
      ['2', 'stage', { stage: 'source_search', message: 'Checking the published package page.' }],
      ['3', 'answer.start', { message_id: 'assistant-1', response_language: 'en' }],
      ['4', 'answer.delta', { text: 'The package lists an outpatient programme. ' }],
      ['5', 'answer.complete', {
        message: {
          id: 'assistant-1', role: 'assistant', content: 'The package lists an outpatient programme. [Package details](https://example.org/package).', created_at: now,
          citations: [{ id: 'source-1', url: 'https://example.org/package', title: 'Outpatient package details', section: 'Programmes' }],
          activity: [{ stage: 'source_search', message: 'Checked the published package page.' }],
          response_language: 'en', outcome: 'answered',
        },
      }],
    ] as const;
    const body = events.map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const loginAudit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(loginAudit.violations.map((violation) => violation.id)).toEqual([]);

  await page.getByLabel('Access secret').fill('smoke-secret');
  await page.getByRole('button', { name: 'Continue securely' }).click();
  await expect(page.getByRole('heading', { name: /Finding answers can feel simpler/ })).toBeVisible();
  const homeAudit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const homeViolations = homeAudit.violations.map((violation) => ({
    id: violation.id,
    nodes: violation.nodes.map((node) => ({
      target: node.target.join(' '),
      contrast: node.any[0]?.data?.contrastRatio,
      foreground: node.any[0]?.data?.fgColor,
      background: node.any[0]?.data?.bgColor,
    })),
  }));
  expect(homeViolations).toEqual([]);

  await page.getByRole('textbox', { name: 'Ask a question' }).fill('Which program details are published?');
  await page.getByRole('textbox', { name: 'Ask a question' }).press('Enter');
  await expect(page.getByText('Outpatient package details')).toBeVisible();
  const transcriptAudit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const transcriptViolations = transcriptAudit.violations.map((violation) => ({
    id: violation.id,
    nodes: violation.nodes.map((node) => ({
      target: node.target.join(' '),
      contrast: node.any[0]?.data?.contrastRatio,
      foreground: node.any[0]?.data?.fgColor,
      background: node.any[0]?.data?.bgColor,
    })),
  }));
  expect(transcriptViolations).toEqual([]);

  const activityToggle = page.getByRole('button', { name: /How this answer came together/ });
  await activityToggle.click();
  await expect(page.getByText('Checked the published package page.')).toBeVisible();
  const activityAudit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const activityViolations = activityAudit.violations.map((violation) => ({
    id: violation.id,
    nodes: violation.nodes.map((node) => ({
      target: node.target.join(' '),
      contrast: node.any[0]?.data?.contrastRatio,
      foreground: node.any[0]?.data?.fgColor,
      background: node.any[0]?.data?.bgColor,
    })),
  }));
  expect(activityViolations).toEqual([]);
});
