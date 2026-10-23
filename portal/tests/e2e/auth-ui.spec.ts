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
