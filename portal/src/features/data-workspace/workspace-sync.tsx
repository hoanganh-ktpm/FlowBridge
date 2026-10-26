// SPDX-License-Identifier: GPL-3.0-or-later
import { createContext, useCallback, useContext, useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { useSession } from '@/app/session';
import { ApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { workspaceCachePrefix as prefix, workspaceStaleEvent } from '@/lib/workspace-cache';

type Version = { key: string; token: string; producer: string; warehouse_id: string | null; org_id: string };
export type ChangeManifest = { schema_version: number; principal_id: string; org_id: string; authorization_stamp: string; enabled: boolean; complete: boolean; versions: Version[]; coverage: string };
const forecastRevision = 'chronos2-29ec3766';
type Saved = { manifest?: ChangeManifest; day?: string; forecastRevision?: string; entries: [string, unknown][]; states: Record<string, unknown>; notices: string[] };
const empty = (): Saved => ({ forecastRevision, entries: [], states: {}, notices: [] });
const day = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
function load(key: string, principal: string): Saved {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || 'null');
    const accepted = value && valid(value.manifest, principal) && Array.isArray(value.entries)
      && value.entries.every((entry: unknown) => Array.isArray(entry) && typeof entry[0] === 'string')
      && value.states && typeof value.states === 'object' && Array.isArray(value.notices);
    if (!accepted) return empty();
    if (value.forecastRevision !== forecastRevision) {
      // Discard previous model calculations once; retain source reads and UI choices.
      value.entries = value.entries.filter(([id]: [string, unknown]) => {
        try { return !['/planning', '/all-warehouses'].includes(JSON.parse(id)[0]); }
        catch { return false; }
      });
      delete value.states['data:plan']; delete value.states['data:applied'];
      value.forecastRevision = forecastRevision;
    }
    return value;
  } catch { return empty(); }
}
function forget(key: string) { try { sessionStorage.removeItem(key); } catch { /* Storage may be disabled. */ } }
function persist(key: string, value: Saved) {
  try {
    // Bound browser storage. Evict old reads; never store access tokens or commands.
    let serialized = JSON.stringify(value);
    while (serialized.length > 2_000_000 && value.entries.length) { value.entries.shift(); serialized = JSON.stringify(value); }
    if (serialized.length <= 2_000_000) sessionStorage.setItem(key, serialized);
    else sessionStorage.removeItem(key);
  } catch { /* The current page still works without persistent storage. */ }
}
function valid(value: ChangeManifest, principal: string) {
  return value?.schema_version === 1 && value.principal_id === principal && typeof value.org_id === 'string' && /^[a-f0-9]{64}$/.test(value.authorization_stamp) && Array.isArray(value.versions) && value.versions.every(v => typeof v.key === 'string' && /^[a-f0-9]{64}$/.test(v.token));
}
const names: Record<string, string> = { sales: 'Bán hàng', warehouse: 'Kho', logistics: 'Vận chuyển', workflow: 'Hồ sơ phối hợp' };
function differences(previous: ChangeManifest, current: ChangeManifest) {
  const before = new Map(previous.versions.map(v => [v.key, v.token]));
  return [...new Set(current.versions.filter(v => before.get(v.key) !== v.token).map(v => names[v.producer] || 'Nguồn dữ liệu'))];
}
const readable = (path: string, body?: unknown) => body === undefined
  ? !/\/(checks|workflow)$/.test(path) && /^\/(products|warehouse-coverage|decision-capabilities|decisions|decision-tasks|forecasts|scenarios)(\/|\?|$)/.test(path)
  : ['/planning-options', '/all-warehouses', '/planning', '/overview', '/records'].includes(path);
type Sync = {
  saved: Saved; save: () => void;
  read: <T>(path: string, body: unknown, work: () => Promise<T>, fresh?: boolean) => Promise<T>;
};
const Context = createContext<Sync | null>(null);
export function useWorkspaceSync() { return useContext(Context); }
export function useWorkspaceState<T>(name: string, initial: T, data = false): [T, Dispatch<SetStateAction<T>>] {
  const sync = useContext(Context), key = (data ? 'data:' : 'ui:') + name;
  const [value, setValue] = useState<T>(() => sync && key in sync.saved.states ? sync.saved.states[key] as T : initial);
  const set = useCallback<Dispatch<SetStateAction<T>>>(next => setValue(previous => {
    const result = typeof next === 'function' ? (next as (p: T) => T)(previous) : next;
    if (sync) { sync.saved.states[key] = result; sync.save(); }
    return result;
  }), [sync, key]);
  return [value, set];
}

export function WorkspaceSync({ children }: { children: ReactNode }) {
  const session = useSession(), principal = session.profile?.user.user_id || session.mode;
  const storageKey = prefix + session.mode + ':' + principal;
  const saved = useRef(load(storageKey, principal));
  const readyRef = useRef(false);
  const displayed = useRef(false);
  const [ready, setReady] = useState(false), [blocked, setBlocked] = useState(false), [issue, setIssue] = useState('');
  const [epoch, setEpoch] = useState(0), [notices, setNotices] = useState<string[]>(saved.current.notices);
  const trusted = useRef(false), generation = useRef(0), pending = useRef<ChangeManifest | null>(null);
  const reads = useRef(new Map<string, number>());
  useEffect(() => { if (blocked) window.dispatchEvent(new Event(workspaceStaleEvent)); }, [blocked]);
  const save = useCallback(() => { if (trusted.current) persist(storageKey, saved.current); }, [storageKey]);
  const clearData = useCallback(() => {
    generation.current++; saved.current.entries = [];
    for (const key of Object.keys(saved.current.states)) if (key.startsWith('data:')) delete saved.current.states[key];
  }, []);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function check() {
      try {
        const current = await session.api.request<ChangeManifest>('/api/v1/data-workspace/changes', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
        if (!active) return;
        if (!valid(current, principal) || !current.enabled || !current.complete) {
          trusted.current = false; clearData(); forget(storageKey);
          setIssue('Chưa theo dõi được đầy đủ thay đổi trong phạm vi được phân công. Bấm làm mới để đọc dữ liệu mới nhất.');
          if (readyRef.current && valid(current, principal)) setBlocked(true);
        } else {
          const previous = pending.current || saved.current.manifest;
          const authChanged = previous && previous.authorization_stamp !== current.authorization_stamp;
          const changed = previous ? differences(previous, current) : [];
          const dateChanged = saved.current.day && saved.current.day !== day();
          trusted.current = true; setIssue('');
          if (authChanged) {
            clearData(); saved.current.states = {}; saved.current.notices = [];
            saved.current.manifest = current; saved.current.day = day(); pending.current = null;
            setNotices([]); setBlocked(false); setEpoch(v => v + 1); save();
          } else if (changed.length || dateChanged) {
            clearData(); pending.current = current; saved.current.day = day();
            const message = dateChanged ? 'Đã sang ngày mới. Cần kiểm tra lại hạn đơn và kế hoạch.' : `${changed.join(', ')} vừa cập nhật dữ liệu liên quan. Kết quả đang xem cần được cập nhật.`;
            saved.current.notices = [...saved.current.notices, message].slice(-5);
            setNotices([...saved.current.notices]); setBlocked(true); save();
          } else if (!pending.current) {
            saved.current.manifest = current; saved.current.day = day(); save();
          }
        }
      } catch (error) {
        if (!active) return;
        trusted.current = false; clearData(); forget(storageKey);
        if (error instanceof ApiError && [401, 403].includes(error.status)) {
          saved.current.states = {}; setEpoch(v => v + 1); setBlocked(true);
          if (error.status === 401) session.invalidate();
          setIssue('Phiên đăng nhập hoặc quyền dữ liệu đã thay đổi. Đăng nhập lại trước khi tiếp tục.');
        } else {
          setIssue('Chưa kiểm tra được thay đổi từ các bộ phận. Kiểm tra kết nối và cập nhật dữ liệu trước khi xử lý tiếp.');
          if (readyRef.current) setBlocked(true);
        }
      } finally {
        if (active) { readyRef.current = true; setReady(true); timer = setTimeout(() => void check(), 8000); }
      }
    }
    void check();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [session.api, session.invalidate, principal, storageKey, clearData, save]);
  const sync = useRef<Sync>({ saved: saved.current, save, read: async (_path, _body, work) => work() });
  sync.current.save = save;
  sync.current.read = async <T,>(path: string, body: unknown, work: () => Promise<T>, fresh = false) => {
    const cacheable = readable(path, body), key = JSON.stringify([path, body ?? null]);
    if (body !== undefined && path.startsWith('/decisions')) {
      // A command can commit a check then report 409; invalidate on attempts,
      // including lost/error responses, rather than only on HTTP success.
      saved.current.entries = saved.current.entries.filter(([id]) => {
        const [cachedPath] = JSON.parse(id) as [string];
        return !cachedPath.startsWith('/decisions') && cachedPath !== '/decision-tasks';
      });
      save();
    }
    if (fresh) saved.current.entries = saved.current.entries.filter(([id]) => id !== key);
    if (trusted.current && cacheable && !fresh) {
      const entry = saved.current.entries.find(([id]) => id === key);
      if (entry) return structuredClone(entry[1]) as T;
    }
    const ticket = generation.current;
    const readNumber = (reads.current.get(key) || 0) + 1;
    reads.current.set(key, readNumber);
    try {
      const result = await work();
      if (!cacheable && body !== undefined) {
        // Saving a simulation/forecast is not a source-data change. Keep the
        // planning snapshot, but refresh result lists and dossier command reads.
        if (path.startsWith('/configurations')) clearData();
        else saved.current.entries = saved.current.entries.filter(([id]) => {
          const [cachedPath] = JSON.parse(id) as [string];
          return path.startsWith('/decisions')
            ? !cachedPath.startsWith('/decisions') && cachedPath !== '/decision-tasks'
            : !cachedPath.startsWith(path.split('/').slice(0, 2).join('/'));
        });
        save();
      }
      // Never persist a cross-organization overview without an authorized event scope.
      const scoped = path !== '/all-warehouses' || (result as { items?: { organization_id: string; warehouse_id: string }[] })?.items?.every(item =>
        item.organization_id === saved.current.manifest?.org_id && saved.current.manifest.versions.some(v => v.warehouse_id === item.warehouse_id));
      if (cacheable && scoped && trusted.current && ticket === generation.current && readNumber === reads.current.get(key)) {
        saved.current.entries = [...saved.current.entries.filter(([id]) => id !== key), [key, result] as [string, unknown]].slice(-150);
        save();
      }
      return result;
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        clearData(); trusted.current = false; saved.current.states = {}; forget(storageKey);
      }
      throw error;
    }
  };
  function refresh() {
    clearData();
    if (pending.current) { saved.current.manifest = pending.current; pending.current = null; }
    saved.current.states['ui:reload-current'] = true;
    saved.current.day = day(); save(); setBlocked(false); setEpoch(v => v + 1);
  }
  if (!ready) return <p role="status" className="p-6">Đang kiểm tra phiên đăng nhập và thay đổi từ các bộ phận…</p>;
  if (!blocked) displayed.current = true;
  return <Context.Provider value={sync.current}>
    {(blocked || issue) && <section className="pw-sync-notice" role="alert" aria-label="Thông báo thay đổi dữ liệu">
      <strong>{blocked ? 'Dữ liệu đang xem cần cập nhật' : 'Theo dõi thay đổi chưa sẵn sàng'}</strong>
      <p>{issue || notices.at(-1)}</p>
      <Button onClick={refresh}>Cập nhật dữ liệu ngay</Button>
      {notices.length > 1 && <details><summary>Thông báo gần đây ({notices.length})</summary>{notices.map((note, i) => <p key={i}>{note}</p>)}</details>}
    </section>}
    <div inert={blocked || undefined} aria-hidden={blocked || undefined} key={epoch}>{displayed.current ? children : null}</div>
  </Context.Provider>;
}
