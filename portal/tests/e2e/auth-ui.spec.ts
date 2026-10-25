import { expect, test } from '@playwright/test'

test('SSO form uses shadcn inputs, shows passwords only on request and posts native fields', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/?auth-preview=login')
  await expect(page.getByRole('heading', { name: 'Chào mừng trở lại' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Đăng ký|Tạo tài khoản/ })).toHaveCount(0)
  const username = page.getByLabel('Email hoặc tên đăng nhập')
  const password = page.getByLabel('Mật khẩu', { exact: true })
  await username.fill('ui-test-user')
  await password.fill('ui-test-password')
  await expect(password).toHaveAttribute('type', 'password')
  await page.getByRole('button', { name: 'Hiện mật khẩu', exact: true }).click()
  await expect(password).toHaveAttribute('type', 'text')
  await page.getByRole('button', { name: 'Ẩn mật khẩu', exact: true }).click()
  await page.getByLabel('Ghi nhớ đăng nhập').check()
  await page.screenshot({ path: 'ref/qa/auth-desktop.png', fullPage: true })
  let body: URLSearchParams | undefined
  await page.route('**/auth-test-submit', route => {
    body = new URLSearchParams(route.request().postData() || '')
    return route.fulfill({ contentType: 'text/html', body: '<h1>Submitted to test identity provider</h1>' })
  })
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Submitted to test identity provider' })).toBeVisible()
  expect(body!.get('username')).toBe('ui-test-user')
  expect(body!.get('password')).toBe('ui-test-password')
  expect(body!.get('rememberMe')).toBe('on')
  expect(errors).toEqual([])
})

test('mobile login stays readable and has no registration action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?auth-preview=login')
  await expect(page.getByRole('button', { name: 'Đăng nhập', exact: true })).toBeVisible()
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'ref/qa/auth-mobile.png', fullPage: true })
  await page.goto('/?auth-preview=error')
  await expect(page.getByText('Tên đăng nhập hoặc mật khẩu không chính xác.', { exact: true })).toBeVisible()
})

test('TOTP enrollment presents a readable native QR and posts only to the identity action', async ({ page }) => {
  const apiCalls: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiCalls.push(request.url()); });
  await page.goto('/?auth-preview=totp');
  await expect(page.getByRole('heading', { name: 'Thiết lập ứng dụng xác thực', exact: true })).toBeVisible();
  const qr = page.getByRole('img', { name: 'Mã QR kết nối ứng dụng xác thực', exact: true });
  await expect(qr).toBeVisible(); await expect(qr).toHaveAttribute('src', /^data:image\/png;base64,/);
  expect((await qr.boundingBox())!.width).toBeGreaterThanOrEqual(180);
  await page.getByLabel('Mã xác thực', { exact: true }).fill('123456');
  await page.getByLabel(/Tên thiết bị/).fill('Điện thoại công việc');
  await page.getByLabel('Đăng xuất các thiết bị khác', { exact: true }).check();
  await page.screenshot({ path: 'ref/qa/totp-enrollment.png', fullPage: true });
  let fields: URLSearchParams | undefined;
  await page.route('**/auth-test-submit', route => {
    fields = new URLSearchParams(route.request().postData() || '');
    return route.fulfill({ contentType: 'text/html', body: '<h1>Identity confirmation received</h1>' });
  });
  await page.getByRole('button', { name: 'Xác nhận và tiếp tục', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Identity confirmation received', exact: true })).toBeVisible();
  expect(fields!.get('totp')).toBe('123456');
  expect(fields!.get('userLabel')).toBe('Điện thoại công việc');
  expect(fields!.get('totpSecret')).toBeTruthy();
  expect(fields!.get('logout-sessions')).toBe('on');
  expect(apiCalls).toEqual([]);
});

test('TOTP enrollment and manual setup fit mobile without horizontal scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?auth-preview=totp');
  await expect(page.getByRole('img', { name: 'Mã QR kết nối ứng dụng xác thực', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'ref/qa/totp-enrollment-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Xác nhận và tiếp tục', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Xác nhận và tiếp tục', exact: true })).toBeInViewport();
  await page.screenshot({ path: 'ref/qa/totp-enrollment-mobile-confirm.png', fullPage: true });
  await page.getByRole('link', { name: 'Không quét được? Nhập khóa thiết lập', exact: true }).click();
  await expect(page.locator('#kc-totp-secret-key')).toBeVisible();
  await expect(page.getByText('Chu kỳ', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Quay lại quét mã QR', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
