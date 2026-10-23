// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useId, useState } from "react";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { BarChart3, Download, Info } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/empty-state";
import { cell, date, downloadCsv, number } from "@/lib/format";
import type { ChartSpec, Dataset } from "@/lib/types";
import { useIsMobile } from "@/hooks/use-mobile";
import { chartData } from "./chart-data";

export function cleanSupplierName(raw: string): string {
  if (!raw) return "";
  let s = String(raw).trim();

  // Pattern 1: Code prefix like "NCC-01 - ", "NCC_01: ", "NCC01 / ", "SUP-123 - ", "PO123: ", "PR-01 - "
  s = s.replace(
    /^(?:\[[A-Za-z0-9_\-]+\]|\([A-Za-z0-9_\-]+\)|(?:NCC|SUP|PO|PR|Mã|Ma|Code)[_\-\s]*[A-Za-z0-9_\-]+|[A-Za-z]{2,4}[_\-][0-9]+)\s*[:\-\/|–—]\s*/i,
    "",
  );

  // Pattern 2: [NCC-01] or (NCC-01) at the start without separator
  s = s.replace(/^(?:\[[A-Za-z0-9_\-]+\]|\([A-Za-z0-9_\-]+\))\s*/i, "");

  // Pattern 3: Code suffix like " (NCC-01)", " [NCC-01]", " - NCC-01", " (Mã: NCC-01)"
  s = s.replace(
    /\s*(?:\((?:NCC|SUP|PO|PR|Mã|Ma|Code)?[_\-\s]*[A-Za-z0-9_\-]+\)|\[(?:NCC|SUP|PO|PR|Mã|Ma|Code)?[_\-\s]*[A-Za-z0-9_\-]+\]|[:\-\/|–—]\s*(?:NCC|SUP|PO|PR|Mã|Ma|Code)[_\-\s]*[A-Za-z0-9_\-]+)$/i,
    "",
  );

  return s.trim() || String(raw).trim();
}

export function formatCompactNumber(v: number): string {
  if (!Number.isFinite(v)) return "";
  const abs = Math.abs(v);
  if (abs >= 1e9) {
    const formatted = (v / 1e9).toFixed(abs % 1e9 === 0 ? 0 : 1);
    return `${formatted.replace(".", ",")} tỷ`;
  }
  if (abs >= 1e6) {
    const formatted = (v / 1e6).toFixed(abs % 1e6 === 0 ? 0 : 1);
    return `${formatted.replace(".", ",")} tr`;
  }
  if (abs >= 1e4) {
    const formatted = (v / 1e3).toFixed(abs % 1e3 === 0 ? 0 : 1);
    return `${formatted.replace(".", ",")}k`;
  }
  return number(v);
}

export function getCategoryDisplayName(
  category: unknown,
  dataset: Dataset,
  chart: ChartSpec,
): string {
  const str = String(category ?? "").trim();
  if (!str) return "—";

  const original = dataset.rows.find(
    (r) => String(r[chart.x_field]) === str,
  );
  if (original) {
    const nameField = Object.keys(original).find((k) =>
      /^(supplier_name|vendor_name|ten_ncc|ten_nha_cung_cap|product_name|ten_san_pham|name)$/i.test(
        k,
      ),
    );
    if (nameField && original[nameField]) {
      return cleanSupplierName(String(original[nameField]));
    }
  }
  return cleanSupplierName(str);
}

export function formatAxisLabel(
  v: unknown,
  maxChars: number,
  isNumberAxis: boolean,
  dataset: Dataset,
  chart: ChartSpec,
): string {
  if (typeof v === "number") {
    return formatCompactNumber(v);
  }
  const str = String(v ?? "").trim();
  if (!str) return "";

  if (isNumberAxis) {
    const parsed = Number(
      str.replace(/\s*(?:VND|USD|EUR|đ|đồng|\/.*)$/i, "").trim(),
    );
    if (Number.isFinite(parsed)) {
      return formatCompactNumber(parsed);
    }
  }

  const name = getCategoryDisplayName(str, dataset, chart);
  if (name.length > maxChars) {
    return name.slice(0, Math.max(1, maxChars - 1)) + "…";
  }
  return name;
}

const colors = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];
export function DataTable({
  dataset,
  fields,
}: {
  dataset: Dataset;
  fields?: string[];
}) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(dataset.rows.length / 50));
  const currentPage = Math.min(page, pages - 1);
  const columns = fields
    ? fields
        .map((field) => dataset.columns.find((c) => c.field === field))
        .filter((c) => !!c)
    : dataset.columns;
  return (
    <div className="table-scroll">
      <Table className="data-table">
        <TableCaption className="sr-only">{dataset.title}</TableCaption>
        <TableHeader>
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c.field}>
                {c.label}
                {c.unit && <small>{c.unit}</small>}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {dataset.rows
            .slice(currentPage * 50, (currentPage + 1) * 50)
            .map((row, i) => (
              <TableRow key={i}>
                {columns.map((c) => (
                  <TableCell
                    key={c.field}
                    className={
                      ["decimal", "number", "integer"].includes(c.type)
                        ? "tabular-nums"
                        : ""
                    }
                  >
                    {cell(row[c.field], c)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
        </TableBody>
      </Table>
      {pages > 1 && (
        <div className="flex items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground">
          <span>
            {currentPage * 50 + 1}–
            {Math.min((currentPage + 1) * 50, dataset.rows.length)} /{" "}
            {dataset.rows.length}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              Trước
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === pages - 1}
              onClick={() => setPage(currentPage + 1)}
            >
              Sau
            </Button>
          </div>
        </div>
      )}
      {!dataset.rows.length && (
        <EmptyState description="Lần truy vấn này không trả về bản ghi." />
      )}
    </div>
  );
}
export function ChartPlot({
  dataset,
  chart,
}: {
  dataset: Dataset;
  chart: ChartSpec;
}) {
  const isMobile = useIsMobile();
  const data = chartData(dataset, chart);
  const horizontal = chart.orientation === "horizontal";
  const first = chart.series[0];
  const hasData = data.some((row) =>
    chart.series.some((s) => row[s.field] !== null),
  );
  if (!dataset.rows.length || !hasData)
    return (
      <EmptyState
        title="Chưa đủ dữ liệu để vẽ biểu đồ"
        description="Giá trị thiếu hoặc mẫu số bằng 0 được giữ nguyên, không thay bằng số 0."
      />
    );
  const tooltip = (
    <Tooltip
      content={({ active, payload, label }) => {
        if (!active || !payload?.length) return null;
        const category = payload[0]?.payload?.[chart.x_field] ?? label;
        const original = dataset.rows.find(
          (r) => String(r[chart.x_field]) === String(category),
        );
        const displayName = getCategoryDisplayName(category, dataset, chart);
        const rawCategory = String(category ?? "").trim();
        const showSubCode =
          rawCategory &&
          rawCategory !== displayName &&
          !displayName.includes(rawCategory);

        return (
          <div className="chart-tooltip">
            <strong>{displayName}</strong>
            {showSubCode && (
              <span className="block text-[10.5px] text-muted-foreground font-normal -mt-0.5 mb-1.5">
                Mã: {rawCategory}
              </span>
            )}
            {payload.map((p, i) => {
              const field = String(p.dataKey ?? first.field);
              const column = dataset.columns.find((c) => c.field === field);
              return (
                <div key={i}>
                  <span
                    style={{ background: p.color || colors[i % colors.length] }}
                  />
                  <span>{String(p.name)}</span>
                  <b>
                    {chart.stacking === "percent"
                      ? `${number(Number(p.value))}% (${cell(original?.[field], column)})`
                      : cell(original?.[field], column)}
                  </b>
                </div>
              );
            })}
          </div>
        );
      }}
    />
  );
  const axisStyle = {
    fill: "var(--fb-text-muted)",
    fontSize: 11,
    fontFamily: "inherit",
  };
  const showLegend =
    chart.series.length > 1 ||
    chart.type === "pie" ||
    chart.type === "donut";
  const yAxisWidth = horizontal ? (isMobile ? 96 : 124) : (isMobile ? 44 : 50);

  const axis = (
    <>
      <CartesianGrid
        stroke="var(--fb-border)"
        strokeDasharray="3 5"
        vertical={false}
      />
      <XAxis
        type={horizontal ? "number" : "category"}
        dataKey={horizontal ? undefined : chart.x_field}
        tickLine={false}
        axisLine={false}
        tick={axisStyle}
        dy={6}
        interval={0}
        domain={
          horizontal && chart.stacking === "percent" ? [0, 100] : undefined
        }
        tickFormatter={(v) =>
          formatAxisLabel(
            v,
            horizontal ? 8 : isMobile ? 11 : 16,
            horizontal,
            dataset,
            chart,
          )
        }
      />
      <YAxis
        type={horizontal ? "category" : "number"}
        dataKey={horizontal ? chart.x_field : undefined}
        width={yAxisWidth}
        tickLine={false}
        axisLine={false}
        tick={axisStyle}
        interval={0}
        domain={
          !horizontal && chart.stacking === "percent" ? [0, 100] : undefined
        }
        tickFormatter={(v) =>
          formatAxisLabel(
            v,
            horizontal ? (isMobile ? 12 : 18) : 8,
            !horizontal,
            dataset,
            chart,
          )
        }
      />
      {tooltip}
      {showLegend && (
        <Legend
          iconType="circle"
          iconSize={6}
          wrapperStyle={{ fontSize: 11, paddingTop: 10 }}
        />
      )}
    </>
  );
  return (
    <div
      className="chart-plot"
      role="img"
      aria-label={`${chart.title}. Chi tiết số liệu có trong thẻ Dữ liệu.`}
    >
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        {chart.type === "bar" ? (
          <BarChart
            data={data}
            layout={horizontal ? "vertical" : "horizontal"}
            margin={{ top: 8, right: 16, left: horizontal ? 4 : 0, bottom: 4 }}
            barGap={4}
            accessibilityLayer
          >
            {axis}
            {chart.series.map((series, i) => (
              <Bar
                key={series.field}
                dataKey={series.field}
                name={series.label}
                fill={colors[i % colors.length]}
                maxBarSize={28}
                radius={
                  chart.stacking === "none"
                    ? horizontal
                      ? [0, 4, 4, 0]
                      : [4, 4, 0, 0]
                    : 0
                }
                stackId={chart.stacking === "none" ? undefined : "total"}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        ) : chart.type === "line" ? (
          <LineChart
            data={data}
            margin={{ top: 8, right: 16, left: 0, bottom: 4 }}
            accessibilityLayer
          >
            {axis}
            {chart.series.map((series, i) => (
              <Line
                key={series.field}
                dataKey={series.field}
                name={series.label}
                stroke={colors[i % colors.length]}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        ) : (
          <PieChart accessibilityLayer>
            <Pie
              data={data}
              dataKey={first.field}
              nameKey={chart.x_field}
              innerRadius={chart.type === "donut" ? "58%" : 0}
              outerRadius="80%"
              paddingAngle={2}
              isAnimationActive={false}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={colors[i % colors.length]} />
              ))}
            </Pie>
            {tooltip}
            {showLegend && (
              <Legend
                iconType="circle"
                iconSize={6}
                wrapperStyle={{ fontSize: 11 }}
              />
            )}
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
export function DataChart({
  dataset,
  chart,
}: {
  dataset: Dataset;
  chart: ChartSpec;
}) {
  const id = useId();
  return (
    <Card className="result-chart gap-0 py-0 shadow-none">
      <Tabs defaultValue="chart">
        <div className="result-chart-heading">
          <div className="flex items-center gap-2.5">
            <span className="chart-heading-icon">
              <BarChart3 size={17} />
            </span>
            <div>
              <h4>{chart.title}</h4>
              <p>
                {chart.y_label && chart.y_unit && chart.y_label !== chart.y_unit
                  ? `${chart.y_label} · ${chart.y_unit}`
                  : chart.y_label || chart.y_unit || ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <TabsList aria-label={`Chế độ xem ${chart.title}`}>
              <TabsTrigger value="chart">Biểu đồ</TabsTrigger>
              <TabsTrigger value="data">Dữ liệu</TabsTrigger>
            </TabsList>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Tải CSV ${chart.title}`}
              onClick={() =>
                downloadCsv(chart.title, dataset.columns, dataset.rows)
              }
            >
              <Download size={15} />
            </Button>
          </div>
        </div>
        <TabsContent value="chart" id={`${id}-chart`}>
          <ChartPlot dataset={dataset} chart={chart} />
        </TabsContent>
        <TabsContent value="data">
          <DataTable dataset={dataset} />
        </TabsContent>
      </Tabs>
      <div className="chart-footnote">
        <Info size={13} />
        <span>
          {dataset.context.complete ? "Dữ liệu đầy đủ" : "Dữ liệu chưa đầy đủ"}{" "}
          · Mốc chốt: {date(dataset.context.as_of)}
          {dataset.columns.some((c) => c.type === "decimal") &&
            " · Giá trị trên trục được làm tròn; bảng giữ số thập phân gốc."}
        </span>
      </div>
      {dataset.context.denominator_definition && (
        <p className="metric-definition">
          {dataset.context.denominator_definition}
        </p>
      )}
    </Card>
  );
}
