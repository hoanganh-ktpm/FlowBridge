// SPDX-License-Identifier: GPL-3.0-or-later
import {useEffect, useRef, useState} from "react";

const base = "/api/admin/knowledge";
const labels = {ready: "Đã đồng bộ", pending: "Chờ xử lý", processing: "Đang xử lý",
  failed: "Đồng bộ lỗi", unindexed: "Chưa đồng bộ", withdrawn: "Đã ngừng sử dụng"};
const stages = {pending: "Chờ xử lý", chunking: "Đang chia đoạn", embedding: "Đang tạo vector",
  publishing: "Đang cập nhật chỉ mục", complete: "Hoàn tất", failed: "Đồng bộ lỗi"};
const active = (job) => ["pending", "processing"].includes(job?.status);
const empty = () => ({filename: "", title: "", content_md: "", supplier_id: "",
  reviewer_user_id: "", report_asof: new Date().toISOString().slice(0, 10), department_id: ""});
const date = (value) => value ? new Date(value).toLocaleString("vi-VN") : "—";

// React escapes text. Raw HTML, scripts, images and Markdown links are never executed.
// Asterisk headings are the same hierarchy supported by the existing RAG splitter.
function Preview({text}) {
  return <div className="knowledge-preview">{text.split("\n").map((line, i) => {
    const match = /^(#{1,3}|\*{1,3})\s+(.+)$/.exec(line);
    if (match) {const Tag = `h${Math.min(match[1].length + 1, 4)}`; return <Tag key={i}>{match[2]}</Tag>;}
    return <p key={i}>{line || "\u00a0"}</p>;
  })}</div>;
}

export function KnowledgeManager({api}) {
  const [items, setItems] = useState([]);
  const [options, setOptions] = useState({suppliers: [], reviewers: [], departments: []});
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(null);
  const [preview, setPreview] = useState(false);
  const [history, setHistory] = useState(null);
  const [jobs, setJobs] = useState({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const alive = useRef(true);
  const gate = useRef(false);
  const jobsRef = useRef({});
  const viewRef = useRef({selected: null, form: null});
  const navigation = useRef(0);
  const refreshSerial = useRef(0);
  viewRef.current = {selected, form};
  function message(text, failed = false) {setNotice(text); setError(failed);}
  function track(entries, fromList = false) {
    const next = {...jobsRef.current};
    for (const entry of entries) {
      if (!entry?.job_id) continue;
      const previous = next[entry.job_id];
      // The poller handles completion notifications and refreshes the open document.
      if (fromList && active(previous) && !active(entry)) continue;
      // An older list/detail response must not put a completed job back in the queue.
      if (previous && !active(previous) && active(entry)) continue;
      if (previous?.updated_at && entry.updated_at && previous.updated_at > entry.updated_at) continue;
      next[entry.job_id] = {...previous, ...entry, poll_error: entry.poll_error ?? ""};
    }
    jobsRef.current = next;
    setJobs(next);
  }
  async function run(work) {
    if (gate.current) return;
    gate.current = true; setBusy(true);
    try {await work();}
    catch (e) {message(e.message, true);}
    finally {gate.current = false; if (alive.current) setBusy(false);}
  }
  async function refresh() {
    const serial = ++refreshSerial.current;
    const [result, opts] = await Promise.all([api(`${base}/documents`), api(`${base}/options`)]);
    if (alive.current && serial === refreshSerial.current) {
      setItems(result.items); setOptions(opts);
      track(result.items.filter((d) => d.job).map((d) => ({...d.job, filename: d.filename})), true);
    }
  }
  async function open(id) {
    const serial = ++navigation.current;
    const result = await api(`${base}/documents/${id}`);
    if (!alive.current || serial !== navigation.current) return;
    setSelected(result); setForm(null); setHistory(null); setPreview(false);
    if (result.job) track([{...result.job, filename: result.filename}]);
  }
  function settled(next) {
    if (next.status === "ready") {
      const r = next.result;
      message(`Hoàn tất ${next.filename ?? "tài liệu"}: ${r.old_chunk_count} → ${r.new_chunk_count} đoạn. ` +
        (next.kind === "delete" ? "Đã xóa tài liệu và các phiên bản/vector." :
          next.kind === "withdraw" ? "Đã ngừng sử dụng tài liệu." : `Phiên bản ${r.revision}.`));
    } else {
      message(`Đồng bộ lỗi ${next.filename ?? "tài liệu"} (${next.error_code}). Bản đã xuất bản được giữ nguyên. Có thể thử lại.`, true);
    }
  }
  async function reconcile(completed = []) {
    const serial = navigation.current;
    const current = viewRef.current;
    const changed = completed.find((j) => j.document_id === current.selected?.document_id);
    if (changed && !current.form) {
      if (changed.status === "ready" && changed.kind === "delete") {
        setSelected(null); setHistory(null);
      } else {
        try {
          const detail = await api(`${base}/documents/${changed.document_id}`);
          if (alive.current && serial === navigation.current && !viewRef.current.form) {
            setSelected(detail); setHistory(null);
          }
        } catch (e) {
          if (alive.current) message(`${e.message} Bấm Tải lại tài liệu để cập nhật.`, true);
        }
      }
    }
    try {await refresh();}
    catch (e) {
      if (alive.current) message(`${e.message} Tác vụ đã được lưu; bấm Tải lại tài liệu để cập nhật.`, true);
    }
  }
  useEffect(() => {
    alive.current = true;
    run(async () => {
      await refresh();
      if (alive.current) message("Đã tải tài liệu từ PostgreSQL.");
    });
    return () => {alive.current = false;};
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer;
    async function poll() {
      const pending = Object.values(jobsRef.current).filter(active);
      if (pending.length) {
        const results = await Promise.allSettled(pending.map(async (j) => ({
          ...await api(`${base}/jobs/${j.job_id}`), filename: j.filename,
        })));
        if (disposed || !alive.current) return;
        const completed = [];
        const updates = results.map((result, i) => {
          if (result.status === "rejected") return {...pending[i], poll_error: "Chưa lấy được trạng thái; sẽ tự thử lại."};
          if (!active(result.value)) completed.push(result.value);
          return result.value;
        });
        track(updates);
        for (const next of completed) settled(next);
        if (completed.length) await reconcile(completed);
      }
      if (!disposed) timer = setTimeout(poll, 1800);
    }
    timer = setTimeout(poll, 1800);
    return () => {disposed = true; clearTimeout(timer);};
  }, [api]);

  const queued = Object.values(jobs).sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const processing = selected && queued.some((j) => j.document_id === selected.document_id && active(j));
  async function accept(path, method, body, filename = form?.filename ?? selected?.filename, preserveEditor = false) {
    const result = {...await api(path, {method, body}), filename};
    if (!alive.current) return;
    if (result.status === "unchanged") {
      message("Không có thay đổi nội dung; không tạo thêm embedding.");
      setForm(null); await open(result.document_id); await refresh(); return;
    }
    track([result]);
    if (!preserveEditor) {setForm(null); setPreview(false); setHistory(null);}
    setSelected((current) => current?.document_id === result.document_id ? {...current, index_status: result.status, job: result} : current);
    if (body?.supplier_id) setOptions((current) => ({...current,
      suppliers: current.suppliers.map((s) => s.supplier_id === body.supplier_id ? {...s, has_document: true} : s)}));
    if (active(result)) message(`Đã đưa ${filename ?? "tài liệu"} vào hàng đợi. Bạn có thể thêm tài liệu khác ngay.`);
    else settled(result);
    // Refresh independently: accepting a queued operation must not wait for indexing
    // or replace another document's editor when an older job finishes.
    void reconcile(active(result) ? [] : [result]);
  }
  async function save(e) {
    e.preventDefault();
    await run(async () => {
      const payload = {filename: form.filename, title: form.title, content_md: form.content_md,
        expected_revision: selected?.revision ?? 0};
      if (!selected) Object.assign(payload, {supplier_id: form.supplier_id,
        reviewer_user_id: form.reviewer_user_id, department_id: form.department_id || null,
        report_asof: new Date(`${form.report_asof}T00:00:00+07:00`).toISOString()});
      await accept(`${base}/documents${selected ? `/${selected.document_id}` : ""}`,
        selected ? "PUT" : "POST", payload);
    });
  }
  async function command(kind) {
    if (kind === "delete" && !window.confirm(`Xóa vĩnh viễn ${selected.filename}, toàn bộ phiên bản và vector? Không thể khôi phục.`)) return;
    if (kind === "withdraw" && !window.confirm("Ngừng sử dụng tài liệu trong RAG? Nội dung lịch sử vẫn được giữ.")) return;
    await run(() => accept(`${base}/documents/${selected.document_id}${kind === "delete" ? "" : `/${kind}`}`,
      kind === "delete" ? "DELETE" : "POST", {expected_revision: selected.revision}));
  }
  async function upload(file) {
    if (!file) return;
    await run(async () => {
      if (!/\.md$/i.test(file.name) || file.size > 2097152) throw new Error("Chọn file .md UTF-8 tối đa 2 MB.");
      let content;
      try {content = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());}
      catch {throw new Error("File phải sử dụng mã hóa UTF-8.");}
      setForm((value) => ({...value, filename: file.name, content_md: content}));
    });
  }
  const bind = (name) => ({value: form?.[name] ?? "", onChange: (e) => setForm({...form, [name]: e.target.value})});
  const visible = items.filter((d) => `${d.filename} ${d.title} ${d.supplier_name}`.toLocaleLowerCase("vi")
    .includes(search.toLocaleLowerCase("vi")) && (!filter || d.index_status === filter));
  return <section id="knowledge-manager" aria-labelledby="knowledge-title" aria-busy={busy}>
    <h2 id="knowledge-title">Quản lý tài liệu RAG</h2>
    <p>Mỗi nhà cung cấp có một tài liệu. Sửa tạo phiên bản mới; các tác vụ phân tích cũ giữ mốc dữ liệu ban đầu.</p>
    <p id="knowledge-message" role="status" aria-live="polite" data-error={error}>{notice}</p>
    <div className="toolbar">
      <button type="button" disabled={busy} onClick={() => {navigation.current++; setSelected(null); setForm(empty()); setHistory(null); setPreview(false);}}>Thêm tài liệu</button>
      <button type="button" disabled={busy} onClick={() => run(async () => {await refresh(); if (selected && !form) await open(selected.document_id);})}>Tải lại tài liệu</button>
    </div>
    {queued.length > 0 && <section aria-label="Hàng đợi đồng bộ RAG" className="knowledge-queue">
      <h3>Hàng đợi đồng bộ RAG</h3>
      <p>{queued.filter((j) => j.status === "pending").length} đang chờ · {queued.filter((j) => j.status === "processing").length} đang xử lý.
        {" "}Bạn có thể thêm tài liệu khác hoặc đóng trang; tác vụ vẫn được lưu trên server.</p>
      <ul>{queued.map((j) => <li key={j.job_id}>
        <strong>{j.filename ?? "Tài liệu"}</strong>{" · "}
        {active(j) ? (stages[j.stage] ?? labels[j.status]) : j.status === "ready" && j.kind === "delete" ? "Đã xóa" :
          j.status === "ready" && j.kind === "withdraw" ? "Đã ngừng sử dụng" : labels[j.status]}
        {j.status === "ready" && j.kind !== "delete" && j.kind !== "withdraw" && j.result &&
          <span> · Phiên bản {j.result.revision} · {j.result.new_chunk_count} đoạn</span>}
        {j.poll_error && <span role="status"> · {j.poll_error}</span>}
        {j.status === "failed" && <>
          <span> · {j.error_code}. Bản đã xuất bản được giữ nguyên.</span>{" "}
          <button type="button" disabled={busy || (form && selected?.document_id === j.document_id) ||
            queued.some((other) => other.document_id === j.document_id && active(other))}
            onClick={() => run(() => accept(`${base}/jobs/${j.job_id}/retry`, "POST", undefined, j.filename, true))}>
            Thử lại {j.filename ?? "tài liệu"}</button>
        </>}
      </li>)}</ul>
    </section>}
    <div className="knowledge-layout">
      <aside aria-label="Danh sách tài liệu">
        <label>Tìm tài liệu<input value={search} onChange={(e) => setSearch(e.target.value)}/></label>
        <label>Trạng thái<select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Tất cả</option>{Object.entries(labels).map(([k, v]) => <option value={k} key={k}>{v}</option>)}
        </select></label>
        <ul>{visible.map((d) => <li key={d.document_id}>
          <button type="button" disabled={busy} aria-pressed={selected?.document_id === d.document_id}
            onClick={() => run(() => open(d.document_id))}>{d.filename}</button>
          <small>{d.supplier_name} · {labels[d.index_status]} · {d.chunk_count} đoạn</small>
        </li>)}</ul>{!visible.length && <p>Không có tài liệu phù hợp.</p>}
      </aside>
      <div className="knowledge-content">
        {selected && <>
          <h3>{selected.title}</h3>
          <p>{selected.filename} · Phiên bản {selected.revision} · {labels[selected.index_status]} · {selected.chunk_count} đoạn</p>
          <p>Cập nhật: {date(selected.updated_at)} · Phạm vi: {selected.scope_type === "global" ? "Toàn hệ thống" : "Phòng ban"}</p>
          <p>Người nhận xét: {selected.metadata?.reviewer_name_snapshot ?? "—"}</p>
          {!form && <div className="toolbar">
            <button type="button" disabled={busy || processing || selected.index_status === "withdrawn"} onClick={() => {setForm({...selected}); setHistory(null);}}>Sửa tài liệu</button>
            <button type="button" disabled={busy || processing || !selected.revision || selected.index_status === "withdrawn"} onClick={() => command("reindex")}>Đồng bộ lại</button>
            <button type="button" disabled={busy || processing || !selected.can_delete || selected.index_status === "withdrawn"} onClick={() => command("delete")}>Xóa tài liệu</button>
            {selected.has_citations && <button type="button" disabled={busy || processing || selected.index_status === "withdrawn"} onClick={() => command("withdraw")}>Ngừng sử dụng</button>}
          </div>}
          {selected.has_citations && <p>Tài liệu đã được báo cáo trích dẫn nên không thể xóa vĩnh viễn.</p>}
          {selected.job?.status === "failed" && <button type="button" disabled={busy || processing || !!form}
            onClick={() => run(() => accept(`${base}/jobs/${selected.job.job_id}/retry`, "POST"))}>Thử lại tác vụ lỗi</button>}
          {!form && <label>Lịch sử phiên bản<select value={history?.version_no ?? ""} disabled={busy || processing}
            onChange={(e) => {const v = e.target.value; if (!v) setHistory(null); else run(async () => setHistory(await api(`${base}/documents/${selected.document_id}/versions/${v}`)));}}>
            <option value="">Bản hiện tại</option>{selected.versions.map((v) => <option key={v.version_no} value={v.version_no}>
              Phiên bản {v.version_no} · {date(v.known_from)} · {v.created_by_subject}</option>)}
          </select></label>}
        </>}
        {form ? <form onSubmit={save}>
          <fieldset disabled={busy || processing}>
            <legend>{selected ? "Chỉnh sửa Markdown" : "Tài liệu mới"}</legend>
            <label>Chọn file Markdown<input type="file" accept=".md,text/markdown" onChange={(e) => upload(e.target.files[0])}/></label>
            <label>Tên file<input required maxLength={160} {...bind("filename")}/></label>
            <label>Tiêu đề<input required maxLength={255} {...bind("title")}/></label>
            {!selected && <>
              <label>Nhà cung cấp<select aria-label="Nhà cung cấp" required {...bind("supplier_id")}><option value="">Chọn nhà cung cấp</option>
                {options.suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id} disabled={s.has_document}>{s.supplier_code} — {s.supplier_name}{s.has_document ? " (đã có tài liệu)" : ""}</option>)}
              </select></label>
              <label>Người nhận xét<select aria-label="Người nhận xét" required {...bind("reviewer_user_id")}><option value="">Chọn người nhận xét</option>
                {options.reviewers.map((u) => <option key={u.user_id} value={u.user_id}>{u.display_name}</option>)}
              </select></label>
              <label>Mốc dữ liệu nhận xét<input type="date" required {...bind("report_asof")}/></label>
              <label>Phạm vi tài liệu<select {...bind("department_id")}><option value="">Toàn hệ thống</option>
                {options.departments.map((d) => <option key={d.department_id} value={d.department_id}>{d.department_name}</option>)}
              </select></label>
            </>}
            <label htmlFor="knowledge-content-md">Nội dung Markdown</label>
            <textarea id="knowledge-content-md" rows={18} required maxLength={2097152} {...bind("content_md")}/>
            <button type="button" onClick={() => setPreview(!preview)}>{preview ? "Ẩn xem trước" : "Xem trước an toàn"}</button>
            {preview && <Preview text={form.content_md}/>}
            <div className="toolbar"><button type="submit">Lưu &amp; Đồng bộ RAG</button>
              <button type="button" onClick={() => {setForm(null); setPreview(false);}}>Hủy chỉnh sửa</button></div>
          </fieldset>
        </form> : selected ? <>
          {history && <p>Đang xem phiên bản {history.version_no} (chỉ đọc).</p>}
          <Preview text={history?.content_md ?? selected.content_md}/>
        </> : <p>Chọn tài liệu để xem hoặc thêm tài liệu mới.</p>}
      </div>
    </div>
  </section>;
}
