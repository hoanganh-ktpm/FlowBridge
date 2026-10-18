// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {useEffect, useRef, useState} from "react";

const initialSession = () => new URL(location.href).searchParams.get("chat_session") ?? "";
const warning = (value) => value === "RAG_REQUIRES_AUTHORIZED_ANALYSIS_JOB" ?
  "Tra cứu tài liệu chưa khả dụng cho hội thoại này. Bạn vẫn có thể hỏi dữ liệu nghiệp vụ." : value;

export function Chat({api}) {
  const [session, setSession] = useState(initialSession);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState([]);
  const [sources, setSources] = useState([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [requestId, setRequestId] = useState("");
  const [requests, setRequests] = useState([]);
  const [requestPage, setRequestPage] = useState(0);
  const [moreRequests, setMoreRequests] = useState(true);
  const bottom = useRef(null);

  function remember(identifier) {
    setSession(identifier);
    const url = new URL(location.href);
    if (identifier) url.searchParams.set("chat_session", identifier);
    else url.searchParams.delete("chat_session");
    window.history.replaceState(null, "", url);
  }
  async function work(action) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (e) { setError(e.message); }
    finally { locked.current = false; setBusy(false); }
  }
  async function history(identifier, selectedPage) {
    const read = (number) => api(`/api/v1/chat/sessions/${encodeURIComponent(identifier)}/messages?page=${number}&page_size=20`);
    let result = await read(selectedPage ?? 1);
    const last = Math.max(1, Math.ceil(result.total / result.page_size));
    if (selectedPage === undefined && last > 1) result = await read(last);
    setMessages(result.items); setPage(result.page); setPages(last);
  }
  useEffect(() => {
    const identifier = initialSession();
    if (identifier) work(() => history(identifier));
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({block: "nearest"}); }, [messages, busy]);

  async function send(event) {
    event.preventDefault();
    if (locked.current || pending || !draft.trim()) return;
    const body = {message: draft.trim(), ...(session ? {session_id: session} : {}),
      ...(!session && requestId ? {request_id: requestId} : {})};
    const turn = {body, key: crypto.randomUUID()};
    setPending(turn); setDraft(""); setWarnings([]); setSources([]);
    setMessages(current => [...current, {role: "user", content: body.message, message_id: turn.key}]);
    await work(() => execute(turn));
  }
  async function execute(turn) {
    const reply = await api("/api/v1/chat/messages", {method: "POST", body: turn.body,
      idempotencyKey: turn.key, timeoutMs: 240000});
    remember(reply.session_id);
    setPending(null); setWarnings(reply.warnings); setSources(reply.sources);
    setMessages(current => [...current, {role: "assistant", content: reply.answer,
      message_id: turn.key + "-reply"}]);
    // A separate history failure must never turn a successful send into a fresh execution.
    try { await history(reply.session_id); }
    catch { setError("Đã nhận câu trả lời nhưng chưa tải lại được lịch sử. Bạn có thể tải lại hội thoại."); }
  }
  function startNew() {
    if (busy) return;
    if (pending && !window.confirm("Lượt gửi trước chưa xác định được kết quả. Tạo hội thoại mới sẽ không hủy lượt đó. Bạn vẫn muốn tiếp tục?")) return;
    remember(""); setMessages([]); setPending(null); setDraft(""); setRequestId("");
    setWarnings([]); setSources([]); setError(""); setPage(1); setPages(1);
  }
  async function loadRequests() {
    const next = requestPage + 1;
    const result = await api(`/api/v1/purchase-requests?page=${next}&page_size=20`);
    setRequests(current => [...current, ...result.items]);
    setRequestPage(next); setMoreRequests(next * result.page_size < result.total);
  }
  return <section id="chat-panel" aria-labelledby="chat-heading" aria-busy={busy}>
    <header className="chat-header"><div><h2 id="chat-heading">Trợ lý FlowBridge</h2>
      <p>Hỏi về sản phẩm, nhà cung cấp hoặc yêu cầu mua hàng. Hãy nêu đủ thông tin trong mỗi câu hỏi.</p></div>
      <button type="button" disabled={busy} onClick={startNew}>Hội thoại mới</button></header>
    <div className="chat-context">
      <label>Yêu cầu mua hàng (tùy chọn)<select disabled={busy || !!session || !!pending}
        value={requestId} onChange={e => setRequestId(e.target.value)}>
        <option value="">Tra cứu chung</option>
        {requests.map(row => <option key={row.request_id} value={row.request_id}>{row.request_code}</option>)}
      </select></label>
      {!session && moreRequests && <button type="button" disabled={busy || !!pending} onClick={() => work(loadRequests)}>
        {requestPage ? "Tải thêm yêu cầu" : "Tải yêu cầu của tôi"}</button>}
      {session && <button type="button" disabled={busy || !!pending} onClick={() => work(async () => {
        setMessages([]); setWarnings([]); setSources([]); await history(session);
      })}>Tải lại hội thoại</button>}
    </div>
    <div className="chat-messages" role="log" aria-label="Nội dung hội thoại" aria-live="polite">
      {!messages.length && <p className="chat-empty">Bạn muốn tra cứu thông tin gì hôm nay?</p>}
      {messages.map(item => <article className={`chat-message chat-${item.role}`} key={item.message_id}>
        <strong>{item.role === "user" ? "Bạn" : "Trợ lý"}</strong><p>{item.content}</p>
      </article>)}
      {busy && pending && <p role="status">Trợ lý đang xử lý…</p>}<div ref={bottom}/>
    </div>
    {pages > 1 && <div className="toolbar"><button type="button" disabled={busy || !!pending || page <= 1}
      onClick={() => work(() => history(session, page - 1))}>Tin trước</button><span>Trang {page}/{pages}</span>
      <button type="button" disabled={busy || !!pending || page >= pages} onClick={() => work(() => history(session, page + 1))}>Tin sau</button></div>}
    {warnings.map((value, index) => <p className="chat-warning" key={index}>{warning(value)}</p>)}
    {!!sources.length && <details><summary>Nguồn tham khảo ({sources.length})</summary>
      <ul>{sources.map((source, index) => <li key={index}>{source.title ?? source.source_uri ?? source.document_id ?? "Tài liệu tham khảo"}</li>)}</ul></details>}
    {error && <p className="chat-error" role="alert">{error}</p>}
    {!busy && pending && <div className="chat-warning"><p>Lượt gửi chưa hoàn tất. Kiểm tra lại sẽ dùng đúng lượt đã gửi, tránh tạo trùng.</p>
      <button type="button" onClick={() => work(() => execute(pending))}>Kiểm tra lại lượt gửi</button></div>}
    <form className="chat-compose" onSubmit={send}>
      <label htmlFor="chat-input">Tin nhắn<textarea id="chat-input" rows={3} maxLength={4000}
        placeholder="Ví dụ: Liệt kê 2 sản phẩm đang hoạt động" value={draft}
        disabled={busy || !!pending} onChange={e => setDraft(e.target.value)} required/></label>
      <button id="chat-send" type="submit" disabled={busy || !!pending || !draft.trim()}>Gửi tin nhắn</button>
    </form>
  </section>;
}
