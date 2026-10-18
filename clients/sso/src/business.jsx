// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {createApi} from "./api.js";
import {KnowledgeManager} from "./knowledge.jsx";

const editable = new Set(["draft", "needs_review", "analysis_failed"]);
const states = {
  draft: "Bản nháp", analysing: "Đang phân tích", needs_review: "Cần xem lại",
  analysis_failed: "Phân tích thất bại", cancelled: "Đã hủy", approved: "Đã duyệt",
  rejected: "Đã từ chối", submitted: "Đã gửi", pending_approval: "Chờ phê duyệt",
  issued: "Đã phát hành", waiting_confirmation: "Chờ xác nhận", confirmed: "Đã xác nhận",
  partially_received: "Đã nhận một phần", delivered: "Đã giao", active: "Hoạt động",
  inactive: "Ngừng hoạt động", blocked: "Bị chặn", open: "Đang mở", completed: "Hoàn tất",
};
const status = (value) => states[value] ?? value ?? "—";
const newLine = (item = {}) => ({_key: crypto.randomUUID(), product_id: "", quantity: "1.0000",
  requested_delivery_date: "", currency: "VND", preferred_supplier_id: "", notes: "", ...item});

function Table({title, rows, columns, onOpen}) {
  return <div className="table-scroll"><table>
    <caption>{title}</caption>
    <thead><tr>{columns.map(([label]) => <th scope="col" key={label}>{label}</th>)}{onOpen && <th scope="col">Thao tác</th>}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>
      {columns.map(([label, key]) => <td key={label}>{(typeof key === "function" ? key(row) : row[key]) ?? "—"}</td>)}
      {onOpen && <td><button type="button" onClick={() => onOpen(row)}>Xem chi tiết</button></td>}
    </tr>)}</tbody>
  </table>{!rows.length && <p>Chưa có dữ liệu trong phạm vi được phép xem.</p>}</div>;
}
function Facts({title, entries}) {
  return <><h3>{title}</h3><dl>{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "—"}</dd></div>)}</dl></>;
}
function Input({label, as: Tag = "input", children, ...props}) {
  return <label>{label}<Tag {...props}>{children}</Tag></label>;
}
function CatalogSelect({label, rows, idKey, nameKey, optional = false, ...props}) {
  const available = rows.filter((r) => r.status === "active" || r[idKey] === props.value);
  return <Input label={label} as="select" required={!optional} {...props}>
    <option value="">{optional ? "Không chỉ định" : "Chọn sản phẩm"}</option>
    {available.map((r) => <option key={r[idKey]} value={r[idKey]}>{r[nameKey]}</option>)}
    {props.value && !available.some((r) => r[idKey] === props.value) && <option value={props.value}>{props.value}</option>}
  </Input>;
}
function OrderDetail({data}) {
  return <><Facts title={data.po_number} entries={[["Trạng thái", status(data.status)], ["Tổng tiền", `${data.total_amount} ${data.currency}`]]}/>
    <Table title="Sản phẩm đặt hàng" rows={data.items} columns={[["Dòng", "line_number"], ["Sản phẩm", "product_id"], ["Số lượng", "ordered_quantity"], ["Đơn vị", "unit"], ["Đơn giá", "unit_price"], ["Thành tiền", "line_total"]]}/>
    <Table title="Lịch giao" rows={data.schedules} columns={[["Đợt", "schedule_number"], ["Số lượng", "scheduled_quantity"], ["Ngày yêu cầu", "requested_delivery_date"], ["Ngày đã hứa", "promised_delivery_date"], ["Trạng thái", (r) => status(r.status)]]}/>
  </>;
}

function Business({apiUrl, keycloak}) {
  const [view, setView] = useState(location.hash === "#knowledge" ? "knowledge" : "employee");
  const [profile, setProfile] = useState(null);
  const profileRef = useRef(null);
  const [notice, setNotice] = useState({text: "", error: false});
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const clientRef = useRef(null);
  if (!clientRef.current) clientRef.current = createApi({apiUrl, keycloak, getActor: () => profileRef.current?.user.user_id});
  const api = clientRef.current.request;
  const [listing, setListing] = useState(null);
  const [proposals, setProposals] = useState(null);
  const [detail, setDetail] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [catalog, setCatalog] = useState({products: [], suppliers: []});
  const [form, setForm] = useState(null);
  const [access, setAccess] = useState({order: "", token: ""});
  const [preview, setPreview] = useState(null);
  const [decision, setDecision] = useState("confirmed");
  const [reason, setReason] = useState("");
  const [scheduleInputs, setScheduleInputs] = useState([]);
  const message = (text, error = false) => setNotice({text, error});
  const has = (code) => profile?.roles.some((role) => role.role_code === code && role.status === "active" &&
    profile.grants.some((grant) => grant.role_id === role.role_id && !grant.revoked_at)) ?? false;
  const canRead = has("EMPLOYEE") || has("APPROVER") || has("WAREHOUSE");
  async function run(work) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); message("Đang xử lý…");
    try { await work(); }
    catch (error) { message(error.message, true); }
    finally { busyRef.current = false; setBusy(false); }
  }
  useEffect(() => {
    if (!keycloak.authenticated) return;
    run(async () => {
      const data = await api("/api/v1/me");
      profileRef.current = data; setProfile(data);
      if (data.roles.some((r) => r.role_code === "APPROVER") && !data.roles.some((r) => r.role_code === "EMPLOYEE")) setView("approver");
      message("Đã kết nối. Chọn thao tác để tải dữ liệu.");
    });
  }, []);

  async function list(kind, page = 1) {
    const paths = {requests: "purchase-requests", orders: "purchase-orders", proposals: "proposals"};
    const data = await api(`/api/v1/${paths[kind]}?page=${page}&page_size=20`);
    if (kind === "proposals") setProposals(data);
    else { setListing({kind, data}); setDetail(null); }
    message("Đã tải dữ liệu.");
  }
  async function allPages(path) {
    const items = [];
    for (let page = 1; ; page++) {
      const data = await api(`${path}?page=${page}&page_size=100`);
      items.push(...data.items);
      if (page * data.page_size >= data.total || !data.items.length) return items;
    }
  }
  async function loadCatalog() {
    const products = await allPages("/api/v1/products");
    const suppliers = await allPages("/api/v1/suppliers");
    const data = {products, suppliers}; setCatalog(data); return data;
  }
  async function openRequest(data = null) {
    await loadCatalog();
    setForm({editing: data, notes: data?.notes ?? "", lines: (data?.items ?? [{}]).map(newLine)});
    message("Điền thông tin và kiểm tra trước khi gửi.");
  }
  async function openDetail(row, kind) {
    const data = await api(`/api/v1/${kind === "orders" ? "purchase-orders" : "purchase-requests"}/${encodeURIComponent(kind === "orders" ? row.purchase_order_id : row.request_id)}`);
    setDetail({kind, data}); message("Đã tải chi tiết.");
  }
  async function saveRequest(event) {
    event.preventDefault();
    await run(async () => {
      const items = form.lines.map((item) => {
        if (!/[1-9]/.test(item.quantity)) throw new Error("Số lượng phải lớn hơn 0.");
        return {product_id: item.product_id, quantity: item.quantity.trim(), currency: item.currency.trim(),
          requested_delivery_date: item.requested_delivery_date,
          preferred_supplier_id: item.preferred_supplier_id || null, notes: item.notes?.trim() || null};
      });
      const body = {source: "web", notes: form.notes.trim() || null, items};
      if (form.editing) body.expected_input_revision = form.editing.input_revision;
      const data = await api("/api/v1/purchase-requests" + (form.editing ? `/${encodeURIComponent(form.editing.request_id)}` : ""), {method: form.editing ? "PATCH" : "POST", body});
      setForm(null); setListing(null); setDetail({kind: "requests", data});
      message("Đã lưu và gửi yêu cầu. Phân tích tự động chờ worker được kết nối.");
    });
  }
  async function commandRequest(data, command) {
    if (!window.confirm(`${command === "cancel" ? "Hủy" : "Gửi lại"} yêu cầu ${data.request_code}?`)) { message("Đã giữ nguyên yêu cầu."); return; }
    const result = await api(`/api/v1/purchase-requests/${encodeURIComponent(data.request_id)}/${command}`, {method: "POST", body: {expected_input_revision: data.input_revision}});
    setForm(null); setListing(null); setDetail({kind: "requests", data: result});
    message(command === "cancel" ? "Đã hủy yêu cầu." : "Đã gửi yêu cầu; phân tích tự động chờ worker.");
  }
  async function openProposal(row) {
    setProposal(await api(`/api/v1/proposals/${encodeURIComponent(row.proposal_id)}`));
    message("Đã tải chi tiết đề xuất.");
  }
  function updateLine(index, name, value) {
    setForm((current) => ({...current, lines: current.lines.map((line, i) => i === index ? {...line, [name]: value} : line)}));
  }
  function updateAccess(name, value) {
    setAccess((current) => ({...current, [name]: value})); setPreview(null); setScheduleInputs([]);
  }
  async function previewOrder(event) {
    event.preventDefault();
    await run(async () => {
      setPreview(null);
      const data = await api(`/api/v1/supplier-confirmations/${encodeURIComponent(access.order.trim())}`, {confirmationToken: access.token});
      setPreview(data); setDecision("confirmed"); setReason("");
      setScheduleInputs(data.schedules.filter((s) => ["open", "confirmed"].includes(s.status)).map((s) => ({...s, confirmed_quantity: s.scheduled_quantity, promised_delivery_date: s.requested_delivery_date})));
      message(data.can_decide ? "Kiểm tra đơn và lịch giao trước khi phản hồi." : "Đơn này không còn nhận phản hồi.");
    });
  }
  async function sendDecision(event) {
    event.preventDefault();
    await run(async () => {
      if (!preview?.can_decide) throw new Error("Hãy xem lại đơn trước khi phản hồi.");
      if (decision === "rejected" && !reason.trim()) throw new Error("Vui lòng nhập lý do từ chối.");
      const items = decision === "rejected" ? [] : scheduleInputs.map((item) => {
        if (item.confirmed_quantity && !/[1-9]/.test(item.confirmed_quantity)) throw new Error("Số lượng xác nhận phải lớn hơn 0.");
        return {schedule_id: item.schedule_id, confirmed_quantity: item.confirmed_quantity.trim() || null, promised_delivery_date: item.promised_delivery_date || null};
      });
      if (!window.confirm(`Gửi phản hồi ${decision === "confirmed" ? "xác nhận" : "từ chối"} đơn ${preview.po_number}? Token chỉ dùng cho một quyết định.`)) { message("Chưa gửi phản hồi."); return; }
      await api(`/api/v1/supplier-confirmations/${encodeURIComponent(access.order.trim())}`, {method: "POST", confirmationToken: access.token, body: {decision, reason: reason.trim() || null, items}});
      setPreview(null); setAccess({...access, token: ""}); setScheduleInputs([]); clientRef.current.clearSupplierRetries();
      message("Đã ghi nhận phản hồi của nhà cung cấp.");
    });
  }
  function Pagination({kind, data}) {
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    return <div><button type="button" disabled={data.page <= 1} onClick={() => run(() => list(kind, data.page - 1))}>Trang trước</button>
      <span> Trang {data.page}/{pages} · {data.total} kết quả </span>
      <button type="button" disabled={data.page >= pages} onClick={() => run(() => list(kind, data.page + 1))}>Trang sau</button></div>;
  }
  const requestColumns = [["Mã yêu cầu", "request_code"], ["Trạng thái", (r) => status(r.status)], ["Phiên bản", "input_revision"], ["Ghi chú", "notes"]];
  const orderColumns = [["Mã đơn", "po_number"], ["Trạng thái", (r) => status(r.status)], ["Tổng tiền", "total_amount"], ["Tiền tệ", "currency"]];
  return <div aria-busy={busy}>
    <p id="business-profile">{profile ? `${profile.user.display_name} · ${profile.roles.map((r) => r.role_name).join(", ") || "Chưa được cấp vai trò"}` : "Đăng nhập để sử dụng nghiệp vụ nội bộ. Nhà cung cấp dùng token xác nhận riêng."}</p>
    <nav aria-label="Khu vực nghiệp vụ">{[["employee", "Nhân viên"], ["approver", "Người duyệt"], ["supplier", "Nhà cung cấp"],
      ...(has("ADMIN") ? [["knowledge", "Tài liệu RAG"]] : [])].map(([key, label]) =>
      <button key={key} type="button" aria-pressed={view === key} disabled={busy} onClick={() => {setView(key); location.hash = key === "knowledge" ? "knowledge" : ""; message("");}}>{label}</button>)}</nav>
    <p id="business-message" role="status" aria-live="polite" data-error={notice.error}>{notice.text}</p>
    <fieldset className="workspace-controls" disabled={busy}>
    {view === "knowledge" && (has("ADMIN") ? <KnowledgeManager api={api}/> : <p>Chỉ quản trị viên được quản lý tài liệu RAG.</p>)}
    <section id="employee" aria-labelledby="employee-title" hidden={view !== "employee"}>
      <h2 id="employee-title">Yêu cầu mua hàng</h2>
      <p>Yêu cầu hiển thị theo quyền tài khoản. Tạo hoặc lưu thay đổi sẽ gửi yêu cầu sang trạng thái đang phân tích.</p>
      <div className="toolbar">
        <button type="button" id="new-request" disabled={!has("EMPLOYEE")} onClick={() => run(() => openRequest())}>Tạo yêu cầu mới</button>
        <button type="button" id="load-requests" disabled={!canRead} onClick={() => run(() => list("requests"))}>Tải yêu cầu</button>
        <button type="button" id="load-orders" disabled={!canRead} onClick={() => run(() => list("orders"))}>Xem đơn đặt hàng</button>
        <button type="button" id="load-catalog" disabled={!canRead} onClick={() => run(async () => {await loadCatalog(); setListing({kind: "catalog"}); setDetail(null); message("Đã tải danh mục.");})}>Tra cứu sản phẩm / nhà cung cấp</button>
      </div>
      <div id="employee-list">{listing?.kind === "catalog" ? <>
        <Table title="Sản phẩm" rows={catalog.products} columns={[["Mã", "product_code"], ["Tên", "product_name"], ["Đơn vị", "unit"], ["Trạng thái", (r) => status(r.status)]]}/>
        <Table title="Nhà cung cấp" rows={catalog.suppliers} columns={[["Mã", "supplier_code"], ["Tên", "supplier_name"], ["Email", "email"], ["Trạng thái", (r) => status(r.status)]]}/>
      </> : listing && <><Table title={listing.kind === "requests" ? "Yêu cầu trong phạm vi quyền" : "Đơn đặt hàng"} rows={listing.data.items} columns={listing.kind === "requests" ? requestColumns : orderColumns} onOpen={(row) => run(() => openDetail(row, listing.kind))}/><Pagination kind={listing.kind} data={listing.data}/></>}</div>
      <div id="request-detail">{detail && (detail.kind === "orders" ? <OrderDetail data={detail.data}/> : <>
        <Facts title={detail.data.request_code} entries={[["Trạng thái", status(detail.data.status)], ["Phiên bản", detail.data.input_revision], ["Ghi chú", detail.data.notes]]}/>
        <Table title="Sản phẩm yêu cầu" rows={detail.data.items} columns={[["Dòng", "line_number"], ["Sản phẩm", (r) => catalog.products.find((p) => p.product_id === r.product_id)?.product_name ?? r.product_id], ["Số lượng", "quantity"], ["Tiền tệ", "currency"], ["Ngày cần giao", "requested_delivery_date"]]}/>
        {has("EMPLOYEE") && detail.data.requested_by === profile.user.user_id && editable.has(detail.data.status) && <>
          <button type="button" onClick={() => run(() => openRequest(detail.data))}>Sửa yêu cầu</button>
          <button type="button" onClick={() => run(() => commandRequest(detail.data, "submit"))}>Gửi lại yêu cầu</button>
          <button type="button" onClick={() => run(() => commandRequest(detail.data, "cancel"))}>Hủy yêu cầu</button>
        </>}<p>Phân tích tự động chỉ chạy khi worker được kết nối.</p>
      </>)}</div>
      <form id="request-form" hidden={!form} onSubmit={saveRequest}>
        <h3 id="request-form-title">{form?.editing ? `Sửa ${form.editing.request_code} · phiên bản ${form.editing.input_revision}` : "Tạo yêu cầu mới"}</h3>
        <p>Giá và tổng tiền do hệ thống xác định. Số lượng dùng dấu chấm, tối đa 4 chữ số thập phân.</p>
        <div id="request-lines">{form?.lines.map((item, index) => {
          const bind = (name) => ({name, value: item[name] ?? "", onChange: (e) => updateLine(index, name, e.target.value)});
          return <fieldset key={item._key}><legend>Sản phẩm {index + 1}</legend><div className="line-fields">
            <CatalogSelect label="Sản phẩm" rows={catalog.products} idKey="product_id" nameKey="product_name" {...bind("product_id")}/>
            <Input label="Số lượng" required inputMode="decimal" pattern="[0-9]{1,14}(\.[0-9]{1,4})?" {...bind("quantity")}/>
            <Input label="Ngày cần giao" type="date" required {...bind("requested_delivery_date")}/>
            <Input label="Tiền tệ" required pattern="[A-Z]{3}" maxLength={3} {...bind("currency")}/>
          </div><CatalogSelect label="Nhà cung cấp ưu tiên (tùy chọn)" optional rows={catalog.suppliers} idKey="supplier_id" nameKey="supplier_name" {...bind("preferred_supplier_id")}/>
            <Input label="Ghi chú dòng" maxLength={2000} {...bind("notes")}/>
            <button type="button" disabled={form.lines.length <= 1} onClick={() => setForm({...form, lines: form.lines.filter((line) => line._key !== item._key)})}>Xóa dòng</button>
          </fieldset>;
        })}</div>
        <button type="button" id="add-line" disabled={form?.lines.length >= 100} onClick={() => setForm({...form, lines: [...form.lines, newLine()]})}>Thêm sản phẩm</button>
        <Input label="Ghi chú chung" as="textarea" id="request-notes" maxLength={2000} rows={2} value={form?.notes ?? ""} onChange={(e) => setForm({...form, notes: e.target.value})}/>
        <button type="submit" id="save-request">{form?.editing ? "Lưu và gửi lại yêu cầu" : "Tạo và gửi yêu cầu"}</button>
        <button type="button" id="close-request" onClick={() => setForm(null)}>Đóng biểu mẫu</button>
      </form>
    </section>
    <section id="approver" aria-labelledby="approver-title" hidden={view !== "approver"}>
      <h2 id="approver-title">Đề xuất mua hàng</h2>
      <p>Xem đề xuất thuộc phòng ban được cấp quyền. Quyết định duyệt/từ chối đang chờ kết nối workflow.</p>
      <button type="button" id="load-proposals" disabled={!has("APPROVER")} onClick={() => run(() => list("proposals"))}>Tải đề xuất</button>
      <div id="approver-list">{proposals && <><Table title="Đề xuất trong phạm vi quyền" rows={proposals.items} columns={[["Mã", "proposal_code"], ["Trạng thái", (r) => status(r.status)], ["Phiên bản", "version_no"], ["Hiện hành", (r) => r.is_current ? "Có" : "Không"]]} onOpen={(row) => run(() => openProposal(row))}/><Pagination kind="proposals" data={proposals}/></>}</div>
      <div id="proposal-detail">{proposal && <>
        <Facts title={proposal.proposal_code} entries={[["Trạng thái", status(proposal.status)], ["Phiên bản đề xuất", proposal.version_no], ["Phiên bản yêu cầu", proposal.input_revision], ["Lý do đề xuất", proposal.reason]]}/>
        <Table title="Chi tiết đề xuất" rows={proposal.items} columns={[["Dòng yêu cầu", "request_item_id"], ["Nhà cung cấp", "selected_supplier_id"], ["Số lượng", "quantity"], ["Đơn giá", "unit_price_snapshot"], ["Tiền tệ", "currency"], ["Ngày giao", "proposed_delivery_date"], ["Lý do", "reason"]]}/>
        <p>Chờ kết nối workflow để xác minh người duyệt và tác vụ. Hiện chỉ xem đề xuất.</p>
        <button type="button" disabled>Phê duyệt (chờ workflow)</button><button type="button" disabled>Từ chối (chờ workflow)</button>
      </>}</div>
    </section>
    <section id="supplier" aria-labelledby="supplier-title" hidden={view !== "supplier"}>
      <h2 id="supplier-title">Xác nhận đơn đặt hàng</h2>
      <p>Nhập ID đơn và token được cấp riêng cho đơn. Không cần đăng nhập tài khoản nhân viên.</p>
      <form id="supplier-access" onSubmit={previewOrder}>
        <Input label="ID đơn đặt hàng" id="supplier-order" required autoComplete="off" pattern="[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}" value={access.order} onChange={(e) => updateAccess("order", e.target.value)}/>
        <Input label="Token xác nhận" id="supplier-token" type="password" required minLength={16} maxLength={512} autoComplete="off" value={access.token} onChange={(e) => updateAccess("token", e.target.value)}/>
        <button type="submit">Xem đơn cần xác nhận</button>
        <button type="button" id="clear-supplier" onClick={() => {setAccess({order: "", token: ""}); setPreview(null); setScheduleInputs([]); clientRef.current.clearSupplierRetries(); message("Đã xóa thông tin xác nhận.");}}>Xóa thông tin</button>
      </form>
      <div id="supplier-preview">{preview && <OrderDetail data={preview}/>}</div>
      <form id="supplier-decision" hidden={!preview?.can_decide} onSubmit={sendDecision}>
        <Input label="Phản hồi" as="select" id="supplier-choice" value={decision} onChange={(e) => setDecision(e.target.value)}><option value="confirmed">Xác nhận đơn</option><option value="rejected">Từ chối đơn</option></Input>
        <div id="supplier-schedules" hidden={decision === "rejected"}>{scheduleInputs.map((item, index) => {
          const bind = (name) => ({name, value: item[name] ?? "", disabled: decision === "rejected", onChange: (e) => setScheduleInputs((current) => current.map((line, i) => i === index ? {...line, [name]: e.target.value} : line))});
          return <fieldset key={item.schedule_id}><legend>Đợt {item.schedule_number} · {item.scheduled_quantity} · ngày yêu cầu {item.requested_delivery_date}</legend>
            <Input label="Số lượng xác nhận (để trống nếu chỉ xác nhận ngày)" inputMode="decimal" pattern="[0-9]{1,14}(\.[0-9]{1,4})?" {...bind("confirmed_quantity")}/>
            <Input label="Ngày hứa giao" type="date" required {...bind("promised_delivery_date")}/>
          </fieldset>;
        })}</div>
        <Input label="Lý do / ghi chú" as="textarea" id="supplier-reason" required={decision === "rejected"} maxLength={2000} rows={2} value={reason} onChange={(e) => setReason(e.target.value)}/>
        <button type="submit">Gửi phản hồi</button>
      </form>
    </section>
    </fieldset>
  </div>;
}

export function createBusiness(options) {
  createRoot(document.getElementById("business-root")).render(<Business {...options}/>);
}
