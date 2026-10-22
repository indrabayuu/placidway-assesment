import { expect, test } from '@playwright/test';

const now = '2026-10-21T08:00:00.000Z';

test('secret login, Arabic response and streaming without crypto.randomUUID', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window.crypto, 'randomUUID', { value: undefined, configurable: true });
  });
  let authenticated = false;
  let eventStreamRequests = 0;
  const eventStreamUrls: string[] = [];
  let submittedMessage = '';

  await page.route('**/api/auth/me', async (route) => {
    if (!authenticated) {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Sign in required.' }) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Clinical Care Team' }) });
  });
  await page.route('**/api/auth/login', async (route) => {
    const body = route.request().postDataJSON() as { secret: string };
    if (body.secret !== 'smoke-secret') {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'That access secret was not recognized.' }) });
      return;
    }
    authenticated = true;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Clinical Care Team' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'conversation-1', title: 'New conversation', response_language: 'ar', created_at: now, updated_at: now }) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route('**/api/knowledge/status', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ready: true, version: 'smoke', updated_at: now, source_count: 2, chunk_count: 6, price_count: 0, refresh_running: false, last_error: null }) });
  });
  await page.route('**/api/conversations/conversation-1/runs', async (route) => {
    const body = route.request().postDataJSON() as { message: string; client_message_id: string };
    expect(body.client_message_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    expect(body).not.toHaveProperty('response_language');
    submittedMessage = body.message;
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ id: 'run-1', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-1/events' }) });
  });
  await page.route('**/api/runs/run-1/events*', async (route) => {
    eventStreamRequests += 1;
    eventStreamUrls.push(route.request().url());
    const events = eventStreamRequests === 1 ? [
      ['1', 'run.started', { run_id: 'run-1', conversation_id: 'conversation-1' }],
      ['2', 'stage', { stage: 'coverage', message: 'Checking requested facts against the trusted care guide.' }],
    ] as const : [
      ['3', 'answer.start', { message_id: 'assistant-1', candidate_id: 'candidate-1', response_language: 'ar' }],
      ['4', 'thinking.delta', { candidate_id: 'candidate-1', text: 'Comparing the documented programme conditions.' }],
      ['5', 'answer.delta', { candidate_id: 'candidate-1', text: 'Unvalidated partial response.' }],
      ['6', 'answer.rejected', { candidate_id: 'candidate-1', reason: 'Revising against sources' }],
      ['7', 'answer.start', { message_id: 'assistant-1', candidate_id: 'candidate-2', response_language: 'ar' }],
      ['8', 'answer.delta', { candidate_id: 'candidate-2', text: ' More incoming words.' }],
      ['9', 'review.complete', { candidate_id: 'candidate-2', accepted: true }],
      ['10', 'answer.complete', {
        message: {
          id: 'assistant-1', role: 'assistant', content: 'الإجابة النهائية المعتمدة.', created_at: now,
          citations: [{ id: 'source-1', url: 'https://example.org/guide', title: 'Trusted care guide', section: 'Planning' }],
          activity: [{ stage: 'coverage', message: 'Checked the trusted care guide.' }],
          response_language: 'ar',
        },
      }],
    ] as const;
    const body = events.map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' },
      body,
    });
  });
  await page.route('**/api/runs/run-1', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'run-1', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-1/events' }) });
  });

  await page.goto('/');
  expect(await page.evaluate(() => typeof window.crypto.randomUUID)).toBe('undefined');
  await page.getByLabel('Access secret').fill('wrong-secret');
  await page.getByRole('button', { name: 'Continue securely' }).click();
  await expect(page.getByRole('alert')).toContainText('not recognized');
  await expect(page.getByLabel('Access secret')).toBeVisible();

  await page.getByLabel('Access secret').fill('smoke-secret');
  await page.getByRole('button', { name: 'Continue securely' }).click();
  await expect(page.getByRole('heading', { name: /Finding answers can feel simpler/ })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Response language' })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Ask a question' }).fill('ما الخيارات المتاحة؟');
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect.poll(() => submittedMessage).toBe('ما الخيارات المتاحة؟');
  const activityToggle = page.getByRole('button', { name: /Working through your question/ });
  await expect(activityToggle).toHaveAttribute('aria-expanded', 'false');
  await activityToggle.click();
  await expect(page.getByText('Checking requested facts against the trusted care guide.')).toBeVisible();
  await activityToggle.click();

  const arabicAnswer = page.locator('.assistant-bubble[dir="rtl"]');
  await expect(arabicAnswer).toContainText('الإجابة النهائية المعتمدة');
  await expect(arabicAnswer).not.toContainText('Unvalidated partial response');
  const thinking = page.locator('.thinking-panel');
  await thinking.locator('summary').click();
  await expect(thinking).toContainText('Comparing the documented programme conditions.');
  await expect(page.locator('.response-status')).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Trusted care guide/ })).toHaveAttribute('rel', 'noopener noreferrer');

  const completedActivityToggle = page.getByRole('button', { name: /How this answer came together/ });
  await expect(completedActivityToggle).toHaveAttribute('aria-expanded', 'false');
  await completedActivityToggle.click();
  await expect(completedActivityToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('Checked the trusted care guide.')).toBeVisible();

  await expect.poll(() => eventStreamRequests).toBe(2);
  expect(new URL(eventStreamUrls[1]).searchParams.get('after')).toBe('2');
  await page.waitForTimeout(3_500);
  expect(eventStreamRequests).toBe(2);
});
