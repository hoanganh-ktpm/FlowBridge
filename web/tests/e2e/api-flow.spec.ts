import { expect, test, type Page } from '@playwright/test'
import delivery from '../../../contracts/agent-hub/examples/delivery-result.json' with { type: 'json' }

async function mockSession(page: Page, signedIn = true) {
  await page.route('**/config.json', route => route.fulfill({ json: { apiUrl: 'http://127.0.0.1:18082', issuer: 'https://identity.example.test/realms/flowbridge', clientId: 'flowbridge-web-a' } }))
  await page.route('**/node_modules/.vite/deps/keycloak-js.js*', route => route.fulfill({ contentType: 'application/javascript', body: `
    export default class Keycloak {
      authenticated = ${signedIn}; token = 'test-caller';
      constructor(config) { window.__oidcConfig = config }
      async init(options) { window.__oidcInit = options; return this.authenticated }
      async updateToken() { return true }
      async login(options) { window.__loginRedirect = options.redirectUri }
      async logout() { window.__loggedOut = true }
    }
  ` }))
}
test('SSO login uses PKCE and a same-origin callback', async ({ page }) => {
  await mockSession(page, false)
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Đăng nhập với SSO' })).toBeEnabled()
  await page.screenshot({ path: 'ref/qa/web-login-new.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Đăng nhập với SSO' }).click()
  const state = await page.evaluate(() => ({ config: (window as any).__oidcConfig, init: (window as any).__oidcInit, redirect: (window as any).__loginRedirect }))
  expect(state.config.clientId).toBe('flowbridge-web-a')
  expect(state.init).toMatchObject({ pkceMethod: 'S256', checkLoginIframe: false, onLoad: 'check-sso' })
  expect(state.redirect).toBe('http://127.0.0.1:5174/')
  expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].some(key => /token/i.test(key)))).toBe(false)
})

test('real API client preserves idempotency after a lost response and resumes SSE', async ({ page }) => {
  await mockSession(page)
  const conversation = { id: 'conv_001', title: 'Phân tích giao hàng', selected_agents: [], revision: 1, created_at: '2026-10-20T01:00:00Z', updated_at: '2026-10-20T01:00:00Z' }
  const result = { ...delivery, run_id: 'run_001', answer: 'Kết quả kiểm thử theo hợp đồng API.' }
  const metadata = { run_id: 'run_001', conversation_id: 'conv_001', status: 'running', selected_agents: [], used_agents: ['database'], dependency_agents: ['database'], accepted_at: '2026-10-20T01:00:00Z', started_at: '2026-10-20T01:00:01Z', finished_at: null, trace_id: 'trace_001' }
  const requestKeys: string[] = []
  const bodies: unknown[] = []
  const replays: (string | undefined)[] = []
  let created = false; let terminal = false; let streamCount = 0
  await page.route('**/api/v1/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname
    expect(request.headers().authorization).toBe('Bearer test-caller')
    if (path.endsWith('/me')) return route.fulfill({ json: { user: { user_id: 'user_1', display_name: 'Nguyễn An', email: null }, roles: [] } })
    if (path.endsWith('/agents')) return route.fulfill({ json: { items: [{ id: 'database', name: 'Dữ liệu', description: 'Tra cứu dữ liệu', available: true }] } })
    if (path.endsWith('/conversations')) {
      if (request.method() === 'POST') { created = true; return route.fulfill({ status: 201, json: conversation }) }
      return route.fulfill({ json: { items: created ? [conversation] : [], next_cursor: null } })
    }
    if (path.endsWith('/conversations/conv_001')) return route.fulfill({ json: conversation })
    if (path.endsWith('/messages')) return route.fulfill({ json: { items: terminal ? [
      { id: 'msg_user', run_id: 'run_001', sequence: 1, role: 'user', text: 'Phân tích giao hàng', question_answers: {}, created_at: conversation.created_at, updated_at: conversation.created_at, result_url: null },
      { id: 'msg_assistant', run_id: 'run_001', sequence: 2, role: 'assistant', text: result.answer, question_answers: {}, created_at: conversation.created_at, updated_at: conversation.created_at, result_url: '/api/v1/agent-hub/runs/run_001' },
    ] : [], next_cursor: null } })
    if (path.endsWith('/conversations/conv_001/runs') && request.method() === 'POST') {
      requestKeys.push(request.headers()['idempotency-key']); bodies.push(request.postDataJSON())
      if (requestKeys.length === 1) return route.abort('failed')
      return route.fulfill({ status: 202, json: { run_id: 'run_001', conversation_id: 'conv_001', message_id: 'msg_user', status: 'queued', result_url: '/api/v1/agent-hub/runs/run_001', metadata_url: '/api/v1/agent-hub/runs/run_001/metadata', events_url: '/api/v1/agent-hub/runs/run_001/events' } })
    }
    if (path.endsWith('/metadata')) return route.fulfill({ json: { ...metadata, status: terminal ? 'completed' : 'running', finished_at: terminal ? '2026-10-20T01:00:03Z' : null } })
    if (path.endsWith('/runs/run_001')) return route.fulfill({ json: terminal ? result : { ...result, status: 'running', answer: '', datasets: [], tables: [], charts: [], sources: [] } })
    if (path.endsWith('/events')) {
      streamCount++; replays.push(request.headers()['last-event-id'])
      const event = (sequence: number, type: string, data: unknown) => `id: evt_${sequence}\nevent: ${type}\ndata: ${JSON.stringify({ schema_version: '1.0', id: `evt_${sequence}`, sequence, type, run_id: 'run_001', occurred_at: '2026-10-20T01:00:01Z', data })}\n\n`
      if (streamCount === 1) return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'tool.started', { span_id: 'tool_1', agent: 'database', label: 'Tra cứu dữ liệu', tool: 'supplier_delivery_statistics' }) })
      terminal = true
      return route.fulfill({ contentType: 'text/event-stream', body: event(2, 'tool.completed', { span_id: 'tool_1', agent: 'database', label: 'Đã tra cứu dữ liệu', outcome: 'success', duration_ms: 2500 }) + event(3, 'run.completed', { status: 'completed' }) })
    }
    return route.fulfill({ status: 404, json: { message: 'Unknown test endpoint' } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Tôi là FlowBridge, tôi có thể giúp bạn/ })).toBeVisible()
  await page.getByRole('textbox', { name: 'Tin nhắn', exact: true }).fill('Phân tích giao hàng')
  await page.getByRole('button', { name: 'Gửi tin nhắn' }).click()
  await expect(page.getByRole('button', { name: 'Kiểm tra lượt gửi' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Tin nhắn', exact: true })).toBeDisabled()
  await expect(page.locator('[data-message-role="pending"]')).toHaveCount(1)
  await page.getByRole('button', { name: 'Kiểm tra lượt gửi' }).click()
  await expect(page.getByText(result.answer, { exact: true })).toBeVisible()
  await expect(page.locator('[data-message-role="pending"]')).toHaveCount(0)
  await expect(page.locator('[data-message-role="user"]')).toHaveCount(1)
  expect(requestKeys).toHaveLength(2)
  expect(requestKeys[0]).toBe(requestKeys[1])
  expect(bodies[0]).toEqual(bodies[1])
  expect(bodies[0]).toMatchObject({ conversation_revision: 1, timezone: 'Asia/Ho_Chi_Minh', message: 'Phân tích giao hàng' })
  expect(replays[1]).toBe('evt_1')
  await page.getByRole('button', { name: 'Hoạt động', exact: true }).click()
  await expect(page.getByText('Đã tra cứu dữ liệu', { exact: true })).toBeVisible()
  await expect(page.getByText('2.5 s', { exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: /Tiến độ giao hàng/ })).toBeVisible()
})

test('send appears immediately before API admission and a rejected message can be edited', async ({ page }) => {
  await mockSession(page)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/me')) return route.fulfill({ json: { user: { user_id: 'test', display_name: 'Kiểm thử', email: null }, roles: [] } })
    if (path.endsWith('/agents')) return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/conversations') && route.request().method() === 'POST') {
      await gate
      return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } })
    }
    return route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Tôi là FlowBridge, tôi có thể giúp bạn/ })).toBeVisible()
  const composer = page.getByRole('textbox', { name: 'Tin nhắn', exact: true })
  await composer.fill('Tra cứu đơn hàng của tôi')
  await page.getByRole('button', { name: 'Gửi tin nhắn', exact: true }).click()
  await expect(page.locator('[data-message-role="pending"]')).toContainText('Tra cứu đơn hàng của tôi')
  await expect(composer).toHaveValue('')
  await expect(page.getByRole('status')).toContainText('Đang gửi tin nhắn')
  await expect(page.getByRole('heading', { name: /Tôi là FlowBridge, tôi có thể giúp bạn/ })).toHaveCount(0)
  await page.screenshot({ path: 'ref/qa/chat-sending.png', fullPage: true, animations: 'disabled' })
  release()
  await expect(page.getByText('Tin nhắn chưa gửi được', { exact: true })).toBeVisible()
  await expect(page.locator('.run-progress')).toHaveCount(0)
  await page.getByRole('button', { name: 'Sửa tin nhắn', exact: true }).click()
  await expect(composer).toHaveValue('Tra cứu đơn hàng của tôi')
  await expect(page.locator('[data-message-role="pending"]')).toHaveCount(0)
})

test('backend failure is readable, keeps its trace and never looks like an active answer', async ({ page }) => {
  await mockSession(page)
  const conversation = { id: 'conv_error', title: 'Lượt xử lý lỗi', selected_agents: [], revision: 1, created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z' }
  const metadata = { run_id: 'run_error', conversation_id: conversation.id, status: 'failed', selected_agents: [], used_agents: [], dependency_agents: [], started_at: conversation.created_at, accepted_at: conversation.created_at, finished_at: conversation.created_at }
  let writes = 0
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') writes++
    if (path.endsWith('/me')) return route.fulfill({ json: { user: { user_id: 'test', display_name: 'Kiểm thử', email: null }, roles: [] } })
    if (path.endsWith('/agents')) return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/conversations')) return route.fulfill({ json: { items: [conversation], next_cursor: null } })
    if (path.endsWith('/conversations/conv_error')) return route.fulfill({ json: conversation })
    if (path.endsWith('/messages')) return route.fulfill({ json: { items: [
      { id: 'msg_error_user', run_id: 'run_error', sequence: 1, role: 'user', text: 'Xin chào', question_answers: {}, result_url: null },
      { id: 'msg_error_assistant', run_id: 'run_error', sequence: 2, role: 'assistant', text: '', question_answers: {}, result_url: '/api/v1/agent-hub/runs/run_error' },
    ], next_cursor: null } })
    if (path.endsWith('/conversations/conv_error/runs')) return route.fulfill({ json: { items: [metadata], next_cursor: null } })
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: '' })
    if (path.endsWith('/runs/run_error')) return route.fulfill({ json: {
      schema_version: '1.0', run_id: 'run_error', status: 'failed', answer: '', datasets: [], charts: [], tables: [], sources: [], warnings: [], questions: [],
      error: { code: 'EXECUTION_FAILED', message: 'Lượt xử lý chưa hoàn tất. Hệ thống không tự chạy lại.', retryable: false, trace_id: 'run_error' },
    } })
    return route.fulfill({ status: 404, json: {} })
  })
  await page.goto('/#chat/conv_error')
  await expect(page.getByRole('alert')).toContainText('Chưa thể hoàn tất câu trả lời')
  await expect(page.locator('.run-progress')).toHaveCount(0)
  await page.getByText('Chi tiết lỗi', { exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('EXECUTION_FAILED')
  await expect(page.getByRole('alert')).toContainText('run_error')
  await page.getByRole('textbox', { name: 'Tin nhắn', exact: true }).fill('Câu hỏi tiếp theo')
  await expect(page.getByRole('button', { name: 'Gửi tin nhắn', exact: true })).toBeEnabled()
  await page.screenshot({ path: 'ref/qa/chat-failure.png', fullPage: true, animations: 'disabled' })
  expect(writes).toBe(0)
})
