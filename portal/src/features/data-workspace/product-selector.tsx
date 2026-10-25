// SPDX-License-Identifier: GPL-3.0-or-later
/** Select a known product; the selected value is always its stable SKU. */
import { NativeSelect } from '@/components/ui/native-select';
export function ProductSelector({ items, value, onChange, disabled = false, id }: {
  items: { sku: string; name?: string | null }[];
  value: string;
  onChange: (sku: string) => void;
  disabled?: boolean;
  id?: string;
}) {
  const sorted = [...items].sort((a, b) => a.sku.localeCompare(b.sku, 'vi', { numeric: true }));
  return <NativeSelect id={id} aria-label="Sản phẩm" value={value} disabled={disabled || !items.length} onChange={e => onChange(e.target.value)}>
    {!value && <option value="">{items.length ? 'Chọn sản phẩm' : 'Cập nhật để tải danh sách sản phẩm'}</option>}
    {sorted.map(p => <option key={p.sku} value={p.sku}>{p.sku} - {p.name || 'Chưa có tên được chia sẻ'}</option>)}
  </NativeSelect>;
}
