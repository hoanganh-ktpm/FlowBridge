import type { ChartSpec, Dataset } from "@/lib/types";

export function chartData(dataset: Dataset, chart: ChartSpec) {
  return dataset.rows.map((row) => {
    const mapped: Record<string, string | number | null> = {
      [chart.x_field]:
        row[chart.x_field] === null
          ? "Chưa có nhãn"
          : String(row[chart.x_field]),
    };
    for (const series of chart.series) {
      const value = row[series.field];
      mapped[series.field] =
        value === null ||
        value === undefined ||
        typeof value === "boolean" ||
        value === "" ||
        !Number.isFinite(Number(value))
          ? null
          : Number(value);
    }
    if (chart.stacking === "percent") {
      const values = chart.series.map((series) => mapped[series.field]);
      const total = values.reduce<number>(
        (sum, value) => sum + (typeof value === "number" ? value : 0),
        0,
      );
      const complete = values.every(
        (value) => typeof value === "number" && value >= 0,
      );
      for (const series of chart.series)
        mapped[series.field] =
          complete && total > 0
            ? (Number(mapped[series.field]) / total) * 100
            : null;
    }
    return mapped;
  });
}
