// SPDX-License-Identifier: GPL-3.0-or-later
// Real keycloak-js adapter against a fake IdP boundary; no real users/tokens.
import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';

let origin: string, issuer: string;
test.beforeEach(({ baseURL }) => { origin = new URL(baseURL!).origin; issuer = origin + '/realms/auth-test'; });
async function authorize(page: Page) {
  await page.route('**/config.json', route => route.fulfill({ json: { apiUrl: origin, issuer, clientId: 'flowbridge-web-a' } }));
  await page.route('**/realms/auth-test/protocol/openid-connect/auth?*', route => route.fulfill({ contentType: 'text/html', body: '<h1>Test IdP</h1>' }));
  await page.goto('/#data');
  await expect(page.getByRole('heading', { name: 'Test IdP' })).toBeVisible();
  return new URL(page.url());
}
function callback(state: string) {
  return `${origin}/#state=${encodeURIComponent(state)}&session_state=fake-session&iss=${encodeURIComponent(issuer)}&code=fake-code`;
}
function jwt(claims: object) {
  return `${Buffer.from('{"alg":"RS256"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.test-signature`;
}

test('real SDK rejects callback with unknown state before exchanging a code', async ({ page }) => {
  const auth = await authorize(page);
  expect(auth.searchParams.get('code_challenge_method')).toBe('S256');
  expect(auth.searchParams.get('nonce')).toBeTruthy();
  let exchanges = 0;
  await page.route('**/realms/auth-test/protocol/openid-connect/token', route => { exchanges++; return route.fulfill({ status: 400, json: { error: 'invalid_grant' } }); });
  await page.goto(callback('attacker-state'));
  await expect(page.getByRole('heading', { name: 'Test IdP' })).toBeVisible();
  expect(exchanges).toBe(0);
  await expect(page.getByRole('button', { name: 'Đăng xuất', exact: true })).toHaveCount(0);
});

test('real SDK sends PKCE verifier and rejects a mismatched ID token nonce', async ({ page }) => {
  const auth = await authorize(page);
  let verifier = '', protectedCalls = 0;
  expect(auth.searchParams.get('response_mode')).toBe('fragment');
  expect(await page.evaluate(state => localStorage.getItem('kc-callback-' + state) !== null, auth.searchParams.get('state'))).toBe(true);
  await page.route('**/api/v1/**', route => { protectedCalls++; return route.fulfill({ status: 401, json: {} }); });
  await page.route('**/realms/auth-test/protocol/openid-connect/token', route => {
    verifier = new URLSearchParams(route.request().postData() || '').get('code_verifier') || '';
    const claims = { sub: 'test', iss: issuer, aud: 'flowbridge-web-a', exp: Date.now() / 1000 + 300, iat: Date.now() / 1000, nonce: 'wrong-nonce' };
    return route.fulfill({ json: { access_token: jwt(claims), id_token: jwt(claims), refresh_token: jwt(claims), token_type: 'Bearer', expires_in: 300 } });
  });
  await page.goto(callback(auth.searchParams.get('state')!));
  await expect.poll(() => verifier).not.toBe('');
  await expect(page.getByText('Không kết nối được SSO.', { exact: false })).toBeVisible();
  expect(createHash('sha256').update(verifier).digest('base64url')).toBe(auth.searchParams.get('code_challenge'));
  expect(protectedCalls).toBe(0);
  await expect(page.getByRole('button', { name: 'Đăng xuất', exact: true })).toHaveCount(0);
});
