// SPDX-License-Identifier: GPL-3.0-or-later
// UI only: Keycloak owns the QR, secret, enrollment action and validation.
import { useState, type ReactNode } from 'react';
import { useInitialize } from 'keycloakify/login/Template.useInitialize';
import { kcSanitize } from 'keycloakify/lib/kcSanitize';
import { AuthShell } from '@/components/auth/auth-shell';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { KcContext } from './KcContext';

export function TotpSetup({ context, message }: {
  context: Extract<KcContext, { pageId: 'login-config-totp.ftl' }>; message: ReactNode;
}) {
  useInitialize({ kcContext: context, doUseDefaultCss: false });
  const [pending, setPending] = useState(false);
  const { totp, mode, url, messagesPerField } = context;
  const manual = mode === 'manual';
  const error = (field: 'totp' | 'userLabel') => messagesPerField.existsError(field) ? <p id={`error-${field}`} role="alert" className="text-xs leading-5 text-destructive" dangerouslySetInnerHTML={{ __html: kcSanitize(messagesPerField.get(field)) }}/> : null;
  return <AuthShell enrollment title="Thiết lập ứng dụng xác thực" description="Kết nối điện thoại để nhận mã đăng nhập cho tài khoản của bạn." footer="Mã QR và khóa thiết lập dùng riêng cho tài khoản này. Giữ chúng trên thiết bị của bạn.">
    {!messagesPerField.existsError('totp', 'userLabel') && message}
    <div className="auth-totp-layout">
      <section className="auth-totp-scan" aria-labelledby="totp-scan-title">
        <div className="auth-step"><span>1</span><div><h2 id="totp-scan-title">Kết nối ứng dụng</h2><p>Mở Google Authenticator, Microsoft Authenticator hoặc ứng dụng xác thực của doanh nghiệp.</p></div></div>
        {manual ? <div className="auth-totp-manual"><span>Khóa thiết lập</span><code id="kc-totp-secret-key">{totp.totpSecretEncoded}</code><dl><div><dt>Loại mã</dt><dd>{totp.policy.type.toUpperCase()}</dd></div><div><dt>Thuật toán</dt><dd>{totp.policy.getAlgorithmKey()}</dd></div><div><dt>Độ dài</dt><dd>{totp.policy.digits} chữ số</dd></div><div><dt>{totp.policy.type === 'totp' ? 'Chu kỳ' : 'Bộ đếm'}</dt><dd>{totp.policy.type === 'totp' ? `${totp.policy.period} giây` : totp.policy.initialCounter}</dd></div></dl></div>
          : <div className="auth-qr-frame"><img id="kc-totp-secret-qr-code" src={`data:image/png;base64,${totp.totpSecretQrCode}`} alt="Mã QR kết nối ứng dụng xác thực"/><span>Chọn thêm tài khoản và quét mã QR này</span></div>}
        <a id={manual ? 'mode-barcode' : 'mode-manual'} href={manual ? totp.qrUrl : totp.manualUrl} className="auth-totp-mode">{manual ? 'Quay lại quét mã QR' : 'Không quét được? Nhập khóa thiết lập'}</a>
      </section>
      <form id="kc-totp-settings-form" action={url.loginAction} method="post" className="auth-totp-confirm" onSubmit={() => setPending(true)}>
        <div className="auth-step"><span>2</span><div><h2>Xác nhận kết nối</h2><p>Nhập mã đang hiển thị trong ứng dụng xác thực.</p></div></div>
        <div className="space-y-2"><Label htmlFor="totp">Mã xác thực</Label><Input id="totp" name="totp" inputMode="numeric" autoComplete="one-time-code" pattern={`[0-9]{${totp.policy.digits}}`} maxLength={totp.policy.digits} placeholder={'0'.repeat(totp.policy.digits)} required readOnly={pending} aria-invalid={messagesPerField.existsError('totp')} aria-describedby={messagesPerField.existsError('totp') ? 'error-totp' : undefined} className="auth-otp-input"/>{error('totp')}</div>
        <div className="space-y-2"><Label htmlFor="userLabel">Tên thiết bị{totp.otpCredentials.length === 0 ? ' (không bắt buộc)' : ''}</Label><Input id="userLabel" name="userLabel" placeholder="Ví dụ: Điện thoại công việc" autoComplete="off" required={totp.otpCredentials.length > 0} readOnly={pending} aria-invalid={messagesPerField.existsError('userLabel')} aria-describedby={messagesPerField.existsError('userLabel') ? 'error-userLabel' : undefined}/>{error('userLabel')}</div>
        <input type="hidden" id="totpSecret" name="totpSecret" value={totp.totpSecret}/>
        {mode && <input type="hidden" id="mode" value={mode}/>}
        <div className="auth-totp-sessions"><Checkbox id="logout-sessions" name="logout-sessions" value="on"/><Label htmlFor="logout-sessions">Đăng xuất các thiết bị khác</Label></div>
        <Button id="saveTOTPBtn" type="submit" disabled={pending} className="w-full">{pending ? 'Đang xác nhận…' : 'Xác nhận và tiếp tục'}</Button>
        {context.isAppInitiatedAction && <Button id="cancelTOTPBtn" type="submit" name="cancel-aia" value="true" formNoValidate variant="outline" className="w-full">Hủy thiết lập</Button>}
      </form>
    </div>
  </AuthShell>;
}
