// SPDX-License-Identifier: GPL-3.0-or-later
import { test, expect, type Page } from '@playwright/test';

async function operator(page: Page) {
  const calls: { path: string; body: unknown; key?: string }[] = [];
  const state = { status: 200 };
  await page.route('**/config.json', r => r.fulfill({ json: { apiUrl: 'http://127.0.0.1:5174', issuer: 'https://identity.example.test/realms/flowbridge', clientId: 'flowbridge-web-a' } }));
  await page.route('**/node_modules/.vite/deps/keycloak-js.js*', r => r.fulfill({ contentType: 'application/javascript', body: `export default class Keycloak { authenticated=true;token='isolated-test';async init(){return true}async updateToken(){return true}async logout(){} }` }));
  await page.route('**/api/v1/**', async r => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/core/me')) return r.fulfill({ json: { principal_id: 'operator-test', platform_role: 'NONE', verified_identity: {} } });
    expect(r.request().headers().authorization).toBe('Bearer isolated-test');
    calls.push({ path, body: r.request().postDataJSON(), key: r.request().headers()['idempotency-key'] });
    return r.fulfill({ status: state.status, json: state.status === 200 ? { result: { id: 'receipt-test', version: 2 } } : { detail: { message: 'Owner từ chối thao tác.' } } });
  });
  await page.goto('/?native-operator=1');
  await expect(page.getByRole('heading', { name: 'Kiểm thử nghiệp vụ trên máy bằng tài khoản đã đăng nhập' })).toBeVisible();
  return { calls, state };
}

test('native operator validates input, retains retry key and discards stale success after owner rejection', async ({ page }) => {
  const { calls, state } = await operator(page);
  const submit = page.getByRole('button', { name: 'Thực hiện bước kiểm thử', exact: true });
  const input = page.getByLabel('Nội dung lệnh JSON');
  await input.fill('[]'); await submit.click();
  await expect(page.getByRole('alert')).toContainText('JSON dạng {...}'); expect(calls).toHaveLength(0);
  await input.fill('{'); await submit.click();
  await expect(page.getByRole('alert')).toContainText('JSON chưa hợp lệ'); expect(calls).toHaveLength(0);
  await page.getByLabel('Thao tác nghiệp vụ').selectOption('warehouse.operations.post_receipt');
  const key = await page.getByLabel('Mã để kiểm tra lại thao tác').inputValue();
  await input.fill('{"accepted_quantity":"40.000"}'); await submit.click();
  await expect(page.getByLabel('Kết quả nghiệp vụ')).toContainText('receipt-test');
  state.status = 403; await submit.click();
  await expect(page.getByRole('alert')).toContainText('HTTP 403');
  await expect(page.getByLabel('Kết quả nghiệp vụ')).toHaveCount(0);
  expect(calls).toEqual([0, 1].map(() => ({ path: '/api/v1/workflows/actions/warehouse.operations.post_receipt/execute', body: { accepted_quantity: '40.000' }, key })));
});

test('physical handoff requires explicit synthetic acceptance and preview cannot enter the operator', async ({ page }) => {
  const { calls } = await operator(page);
  await page.getByLabel('Thao tác nghiệp vụ').selectOption('warehouse.operations.record_carrier_handoff');
  const submit = page.getByRole('button', { name: 'Thực hiện bước kiểm thử', exact: true });
  await expect(submit).toBeDisabled(); expect(calls).toHaveLength(0);
  await page.getByRole('checkbox').check(); await submit.click();
  await expect(page.getByLabel('Kết quả nghiệp vụ')).toContainText('receipt-test');
  await page.getByLabel('Thao tác nghiệp vụ').selectOption('warehouse.operations.inventory_read');
  await page.getByLabel('Thao tác nghiệp vụ').selectOption('warehouse.operations.record_carrier_handoff');
  await expect(page.getByRole('checkbox')).not.toBeChecked(); await expect(submit).toBeDisabled();
  await page.goto('/?preview=1&native-operator=1');
  await expect(page.getByRole('heading', { name: 'Kiểm thử nghiệp vụ trên máy bằng tài khoản đã đăng nhập' })).toHaveCount(0);
  expect(calls).toHaveLength(1);
});

test('leaving native operator restores the Workspace without replacing the SSO session', async ({ page }) => {
  await operator(page);
  await page.getByRole('button', { name: 'Về trang dữ liệu', exact: true }).click();
  await expect(page).toHaveURL(/\/#data$/);
  await expect(page.getByRole('heading', { name: 'Kiểm thử nghiệp vụ trên máy bằng tài khoản đã đăng nhập' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Thực hiện bước kiểm thử', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('flowbridge.dev.native-owner'))).toBeNull();
});
