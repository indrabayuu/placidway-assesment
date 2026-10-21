import { expect, test } from '@playwright/test';

const now = '2026-10-21T08:00:00.000Z';
const conversation = {
  id: 'conversation-1', title: 'Treatment program details', response_language: 'en' as const, created_at: now, updated_at: now,
};

test('restored interrupted run retries the last user message', async ({ page }) => {
  let retriedMessage = '';
  let runCount = 0;
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'client', role: 'client', name: 'Reviewer' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [conversation] }) });
  });
  await page.route('**/api/conversations/conversation-1/messages', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      items: [{ id: 'user-1', role: 'user', content: 'What is included in the outpatient program?', created_at: now, citations: [], activity: [] }],
      active_run: { id: 'run-1', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-1/events' },
    }) });
  });
  await page.route('**/api/runs/run-1/events', async (route) => {
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: `id: 1\nevent: run.started\ndata: {"run_id":"run-1","conversation_id":"conversation-1"}\n\n` });
  });
  await page.route('**/api/runs/run-1', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'run-1', conversation_id: 'conversation-1', status: 'interrupted', created_at: now, error: 'The previous run was interrupted.', events_url: '/api/runs/run-1/events' }) });
  });
  await page.route('**/api/conversations/conversation-1/runs', async (route) => {
    runCount += 1;
    const requestBody = route.request().postDataJSON() as { message: string; client_message_id: string; response_language: string };
    retriedMessage = requestBody.message;
    expect(requestBody.client_message_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(requestBody.response_language).toBe('en');
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ id: 'run-2', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-2/events' }) });
  });
  await page.route('**/api/runs/run-2/events', async (route) => {
    const message = { id: 'assistant-2', role: 'assistant', content: 'The retry completed.', created_at: now, citations: [], activity: [], response_language: 'en' };
    const events = [
      ['1', 'run.started', { run_id: 'run-2', conversation_id: 'conversation-1' }],
      ['2', 'answer.start', { message_id: 'assistant-2', response_language: 'en' }],
      ['3', 'answer.complete', { message }],
    ] as const;
    const body = events.map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Treatment program details/ }).click();
  await expect(page.getByRole('alert')).toContainText('interrupted');
  await page.getByRole('button', { name: /Try again/ }).click();
  await expect.poll(() => retriedMessage).toBe('What is included in the outpatient program?');
  await expect.poll(() => runCount).toBe(1);
  await expect(page.getByText('The retry completed.')).toBeVisible();
});

test('terminal history restores retry without reattaching its completed stream', async ({ page }) => {
  const prompt = 'What is included in the outpatient program?';
  const userMessageId = 'user-message-1';
  let oldRunEventRequests = 0;
  let retryEventRequests = 0;
  let retriedMessage = '';
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'client', role: 'client', name: 'Reviewer' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [conversation] }) });
  });
  await page.route('**/api/conversations/conversation-1/messages', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      items: [{ id: userMessageId, role: 'user', content: prompt, created_at: now, citations: [], activity: [] }],
      active_run: null,
      latest_run: {
        id: 'run-old', conversation_id: 'conversation-1', status: 'interrupted', created_at: now,
        error: 'Server stopped; please retry.', events_url: '/api/runs/run-old/events',
        user_message_id: userMessageId, input: prompt, response_language: 'en', retryable: true,
      },
    }) });
  });
  await page.route('**/api/runs/run-old/events*', async (route) => {
    oldRunEventRequests += 1;
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: '' });
  });
  await page.route('**/api/conversations/conversation-1/runs', async (route) => {
    retriedMessage = (route.request().postDataJSON() as { message: string }).message;
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ id: 'run-new', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-new/events' }) });
  });
  await page.route('**/api/runs/run-new/events', async (route) => {
    retryEventRequests += 1;
    const message = { id: 'assistant-new', role: 'assistant', content: 'The retry completed.', created_at: now, citations: [], activity: [], response_language: 'en' };
    const events = [
      ['1', 'run.started', { run_id: 'run-new', conversation_id: 'conversation-1' }],
      ['2', 'answer.start', { message_id: 'assistant-new', response_language: 'en' }],
      ['3', 'answer.complete', { message }],
    ] as const;
    const body = events.map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Treatment program details/ }).click();
  await expect(page.getByRole('alert')).toContainText('interrupted');
  await expect(page.getByRole('button', { name: /Try again/ })).toBeVisible();
  expect(oldRunEventRequests).toBe(0);
  await page.getByRole('button', { name: /Try again/ }).click();
  await expect.poll(() => retriedMessage).toBe(prompt);
  await expect(page.getByText('The retry completed.')).toBeVisible();
  expect(oldRunEventRequests).toBe(0);
  expect(retryEventRequests).toBe(1);
});

test('expired protected request returns to secret-only sign in', async ({ page }) => {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owner', role: 'owner', name: 'Workspace owner' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route('**/api/knowledge/status', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ready: true, version: 'smoke', updated_at: now, source_count: 7, chunk_count: 54, price_count: 54, refresh_running: false, last_error: null }) });
  });
  await page.route('**/api/admin/knowledge/refresh', async (route) => {
    await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Session expired.' }) });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Refresh knowledge sources' }).click();
  await expect(page.getByLabel('Access secret')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Your session has expired');
  await expect(page.getByLabel('Username')).toHaveCount(0);
});

test('session expiry received on an open event stream returns to sign in', async ({ page }) => {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'client', role: 'client', name: 'Reviewer' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [conversation] }) });
  });
  await page.route('**/api/conversations/conversation-1/messages', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      items: [{ id: 'user-1', role: 'user', content: 'What is included in the outpatient program?', created_at: now, citations: [], activity: [] }],
      active_run: { id: 'run-expired', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-expired/events' },
    }) });
  });
  await page.route('**/api/runs/run-expired/events', async (route) => {
    const body = [
      `id: 1\nevent: run.started\ndata: ${JSON.stringify({ run_id: 'run-expired', conversation_id: 'conversation-1' })}\n\n`,
      `event: run.error\ndata: ${JSON.stringify({ message: 'Session expired. Please sign in again.', retryable: false })}\n\n`,
    ].join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Treatment program details/ }).click();
  await expect(page.getByLabel('Access secret')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Your session has expired');
});

test('failed run recovered by status check exposes an explicit retry', async ({ page }) => {
  let eventRequests = 0;
  let retriedMessage = '';
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'client', role: 'client', name: 'Reviewer' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [conversation] }) });
  });
  await page.route('**/api/conversations/conversation-1/messages', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      items: [{ id: 'user-1', role: 'user', content: 'What is included in the outpatient program?', created_at: now, citations: [], activity: [] }],
      active_run: { id: 'run-failed', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-failed/events' },
    }) });
  });
  await page.route('**/api/runs/run-failed/events', async (route) => {
    eventRequests += 1;
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: `id: 1\nevent: run.started\ndata: ${JSON.stringify({ run_id: 'run-failed', conversation_id: 'conversation-1' })}\n\n` });
  });
  await page.route('**/api/runs/run-failed', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'run-failed', conversation_id: 'conversation-1', status: 'failed', created_at: now, error: 'The answer could not be completed.', events_url: '/api/runs/run-failed/events' }) });
  });
  await page.route('**/api/conversations/conversation-1/runs', async (route) => {
    retriedMessage = (route.request().postDataJSON() as { message: string }).message;
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ id: 'run-retry', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-retry/events' }) });
  });
  await page.route('**/api/runs/run-retry/events', async (route) => {
    const message = { id: 'assistant-retry', role: 'assistant', content: 'The retry completed.', created_at: now, citations: [], activity: [], response_language: 'en' };
    const body = [
      ['1', 'run.started', { run_id: 'run-retry', conversation_id: 'conversation-1' }],
      ['2', 'answer.start', { message_id: 'assistant-retry', response_language: 'en' }],
      ['3', 'answer.complete', { message }],
    ].map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Treatment program details/ }).click();
  await expect(page.getByRole('alert')).toContainText('could not be completed');
  await page.getByRole('button', { name: /Try again/ }).click();
  await expect.poll(() => eventRequests).toBe(1);
  await expect.poll(() => retriedMessage).toBe('What is included in the outpatient program?');
  await expect(page.getByText('The retry completed.')).toBeVisible();
});

test('manual scroll position is preserved when a restored answer grows', async ({ page }) => {
  let eventRequests = 0;
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'client', role: 'client', name: 'Reviewer' }) });
  });
  await page.route('**/api/conversations', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [conversation] }) });
  });
  await page.route('**/api/conversations/conversation-1/messages', async (route) => {
    const items = Array.from({ length: 10 }, (_, index) => ({
      id: `message-${index}`,
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: `Message ${index}: ${'A long published program description. '.repeat(12)}`,
      created_at: now,
      citations: [],
      activity: [],
      response_language: 'en',
    }));
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      items,
      active_run: { id: 'run-scroll', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-scroll/events' },
    }) });
  });
  await page.route('**/api/runs/run-scroll/events*', async (route) => {
    eventRequests += 1;
    const events = eventRequests === 1
      ? [['1', 'run.started', { run_id: 'run-scroll', conversation_id: 'conversation-1' }], ['2', 'stage', { stage: 'source_search', message: 'Searching the source.' }]] as const
      : [['3', 'answer.start', { message_id: 'assistant-scroll', response_language: 'en' }], ['4', 'answer.delta', { text: 'New validated response text.' }], ['5', 'answer.complete', { message: { id: 'assistant-scroll', role: 'assistant', content: 'New validated response text.', created_at: now, citations: [], activity: [], response_language: 'en' } }]] as const;
    const body = events.map(([id, name, data]) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
  });
  await page.route('**/api/runs/run-scroll', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'run-scroll', conversation_id: 'conversation-1', status: 'running', created_at: now, error: null, events_url: '/api/runs/run-scroll/events' }) });
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Treatment program details/ }).click();
  const transcript = page.locator('.conversation-column');
  await expect(page.getByText('Reconnecting to your response…')).toBeVisible();
  await transcript.evaluate((element) => {
    element.scrollTop = 0;
    element.dispatchEvent(new Event('scroll'));
  });
  await expect.poll(() => eventRequests).toBe(2);
  await expect(page.getByText('New validated response text.')).toBeVisible();
  const scrollTop = await transcript.evaluate((element) => element.scrollTop);
  expect(scrollTop).toBeLessThan(5);
});
