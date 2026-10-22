export type AgentId = "database" | "prediction" | "knowledge";
export type RunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "needs_input"
  | "failed"
  | "cancelled";
export interface Agent {
  id: AgentId;
  name: string;
  description: string;
  available: boolean;
  unavailable_reason?: string;
}
export interface Conversation {
  id: string;
  title: string;
  selected_agents: AgentId[];
  revision: number;
  request_id?: string | null;
  created_at: string;
  updated_at: string;
}
export interface Message {
  id: string;
  run_id: string;
  sequence: number;
  role: "user" | "assistant";
  text: string;
  question_answers: Record<string, string | number>;
  created_at: string;
  updated_at: string;
  result_url: string | null;
}
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}
export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}
export type Scalar = string | number | boolean | null;
export interface Column {
  field: string;
  label: string;
  type: string;
  nullable?: boolean;
  unit?: string;
  currency?: string;
  scale?: number;
  format?: string;
}
export interface Dataset {
  id: string;
  title: string;
  columns: Column[];
  rows: Record<string, Scalar>[];
  context: {
    as_of: string | null;
    complete: boolean;
    period_from?: string;
    period_to?: string;
    metric_definition?: string;
    denominator_definition?: string;
    filters?: Record<string, Scalar>;
  };
  source_ids: string[];
}
export interface ChartSpec {
  id: string;
  title: string;
  dataset_id: string;
  type: "bar" | "line" | "pie" | "donut";
  orientation: "vertical" | "horizontal";
  stacking: "none" | "normal" | "percent";
  x_field: string;
  x_label: string;
  y_label: string;
  y_unit: string;
  series: { field: string; label: string }[];
}
export interface Source {
  id: string;
  kind: "tool" | "document" | "url";
  title?: string;
  tool?: string;
  document_id?: string;
  version?: string;
  excerpt?: string;
  url?: string;
}
export interface Question {
  id: string;
  field: string;
  prompt: string;
  kind: "text" | "number" | "date" | "single_choice";
  required: boolean;
  options?: { value: string; label: string }[];
}
// View models of contracts/portal/schemas/run-result.schema.json; no independent JSON schema.
export interface RunResult {
  schema_version: string;
  run_id: string;
  status: RunStatus;
  answer: string;
  datasets: Dataset[];
  charts: ChartSpec[];
  tables: { id: string; title: string; dataset_id: string; fields: string[] }[];
  sources: Source[];
  warnings: { code: string; message: string; dataset_id?: string }[];
  questions: Question[];
  error: {
    code: string;
    message: string;
    retryable: boolean;
    trace_id: string;
  } | null;
}
export interface RunMetadata {
  run_id: string;
  conversation_id: string;
  status: RunStatus;
  selected_agents: AgentId[];
  used_agents: AgentId[];
  dependency_agents: AgentId[];
  accepted_at: string;
  started_at: string | null;
  finished_at: string | null;
  trace_id: string;
  result_url: string;
  events_url: string;
}
export interface AcceptedRun {
  run_id: string;
  conversation_id: string;
  message_id: string;
  status: RunStatus;
  result_url: string;
  metadata_url: string;
  events_url: string;
}
export interface RunEvent {
  schema_version: string;
  id: string;
  sequence: number;
  type: string;
  run_id: string;
  occurred_at: string;
  data: {
    status?: RunStatus;
    span_id?: string;
    parent_span_id?: string | null;
    agent?: string;
    tool?: string;
    label?: string;
    outcome?: string;
    duration_ms?: number;
    error?: { code: string; message: string; trace_id: string };
    result_url?: string;
  };
}
export interface Activity {
  id: string;
  parentId?: string | null;
  agent?: string;
  tool?: string;
  label: string;
  status: string;
  startedAt: string;
  duration?: number;
  error?: string;
}
export interface Supplier {
  supplier_id: string;
  supplier_code: string;
  supplier_name: string;
  email: string | null;
  phone: string | null;
  city: string | null;
  country_of_origin: string | null;
  status: string;
}
export interface Order {
  purchase_order_id: string;
  po_number: string;
  supplier_id: string;
  request_id: string;
  order_date: string;
  status: string;
  currency: string;
  total_amount: string;
  created_at: string;
}
export interface PurchaseRequest {
  request_id: string;
  request_code: string;
  status: string;
  created_at: string;
  notes: string | null;
  items: {
    quantity: string;
    product_id: string;
    requested_delivery_date: string;
  }[];
}
export interface Profile {
  user: { user_id: string; display_name: string; email: string | null };
  roles: { role_code: string }[];
}
export interface CreateRun {
  conversation_revision: number;
  timezone: string;
  message?: string;
  response_to_run_id?: string;
  question_answers?: Record<string, string | number>;
}
export const isTerminal = (status: RunStatus) =>
  ["completed", "needs_input", "failed", "cancelled"].includes(status);
