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
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const alive = useRef(true);
  const gate = useRef(false);
  function message(text, failed = false) {setNotice(text); setError(failed);}
  async function run(work) {
    if (gate.current) return;
    gate.current = true; setBusy(true);
    try {await work();}
    catch (e) {message(e.message, true);}
    finally {gate.current = false; if (alive.current) setBusy(false);}
  }
  async function refresh() {
    const [result, opts] = await Promise.all([api(`${base}/documents`), api(`${base}/options`)]);
    if (alive.current) {setItems(result.items); setOptions(opts);}
  }
  async function open(id) {
    const result = await api(`${base}/documents/${id}`);
    if (!alive.current) return;
    setSelected(result); setForm(null); setHistory(null); setPreview(false);
    setJob(active(result.job) ? result.job : null);
  }
  async function settled(next) {
    if (next.status === "ready") {
      const r = next.result;
      message(`Hoàn tất: ${r.old_chunk_count} → ${r.new_chunk_count} đoạn. ` +
        (next.kind === "delete" ? "Đã xóa tài liệu và các phiên bản/vector." :
          next.kind === "withdraw" ? "Đã ngừng sử dụng tài liệu." : `Phiên bản ${r.revision}.`));
      setForm(null); setHistory(null); setJob(null);
      if (next.kind === "delete") setSelected(null);
      else await open(next.document_id);
    } else {
      message(`Đồng bộ lỗi (${next.error_code}). Bản đã xuất bản được giữ nguyên. Có thể thử lại.`, true);
      await open(next.document_id);
    }
    await refresh();
  }
  useEffect(() => {
    alive.current = true;
    run(async () => {
      const [list, opts] = await Promise.all([api(`${base}/documents`), api(`${base}/options`)]);
      if (alive.current) {setItems(list.items); setOptions(opts); message("Đã tải tài liệu từ PostgreSQL.");}
    });
    return () => {alive.current = false;};
  }, []);

  useEffect(() => {
    if (!active(job)) return;
    let disposed = false;
    const timer = setTimeout(async () => {
      try {
        const next = await api(`${base}/jobs/${job.job_id}`);
        if (disposed || !alive.current) return;
        setJob(next);
        if (!active(next)) await settled(next);
      } catch (e) {
        if (!disposed) {message(`${e.message} Bấm Tải lại để xem trạng thái tác vụ đã lưu.`, true); setJob(null);}
      }
    }, 1800);
    return () => {disposed = true; clearTimeout(timer);};
  }, [job]);

  const processing = active(job);
  async function accept(path, method, body) {
    const result = await api(path, {method, body});
    if (result.status === "unchanged") {
      message("Không có thay đổi nội dung; không tạo thêm embedding.");
      setForm(null); await open(result.document_id); await refresh(); return;
    }
    if (!active(result)) {await settled(result); return;}
    setJob(result);
    setSelected((current) => current ? {...current, index_status: result.status, job: result} : null);
    await refresh();
    message("Đã tiếp nhận. Chỉ hiện Đã đồng bộ khi dữ liệu và vector được xác minh.");
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
  return <section id="knowledge-manager" aria-labelledby="knowledge-title" aria-busy={busy || processing}>
    <h2 id="knowledge-title">Quản lý tài liệu RAG</h2>
    <p>Mỗi nhà cung cấp có một tài liệu. Sửa tạo phiên bản mới; các tác vụ phân tích cũ giữ mốc dữ liệu ban đầu.</p>
    <p id="knowledge-message" role="status" aria-live="polite" data-error={error}>{notice}</p>
    <div className="toolbar">
      <button type="button" disabled={busy || processing} onClick={() => {setSelected(null); setForm(empty()); setHistory(null); setJob(null);}}>Thêm tài liệu</button>
      <button type="button" disabled={busy || processing} onClick={() => run(async () => {await refresh(); if (selected) await open(selected.document_id);})}>Tải lại tài liệu</button>
    </div>
    {active(job) && <p role="status">{stages[job.stage] ?? labels[job.status]}… Có thể đóng trang; tác vụ được lưu trên server.</p>}
    <div className="knowledge-layout">
      <aside aria-label="Danh sách tài liệu">
        <label>Tìm tài liệu<input value={search} onChange={(e) => setSearch(e.target.value)}/></label>
        <label>Trạng thái<select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Tất cả</option>{Object.entries(labels).map(([k, v]) => <option value={k} key={k}>{v}</option>)}
        </select></label>
        <ul>{visible.map((d) => <li key={d.document_id}>
          <button type="button" disabled={busy || processing} aria-pressed={selected?.document_id === d.document_id}
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
          {selected.job?.status === "failed" && <button type="button" disabled={busy || processing}
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
            <label>Nội dung Markdown<textarea rows={18} required maxLength={2097152} {...bind("content_md")}/></label>
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
