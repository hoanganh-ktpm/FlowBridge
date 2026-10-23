import { Database, TrendingUp, BookOpen, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export const agentNames: Record<string, string> = {
  database: "Dữ liệu",
  prediction: "Dự đoán",
  knowledge: "Tri thức",
  central: "Trung tâm",
};
export function AgentIcon({
  id,
  className,
}: {
  id: string;
  className?: string;
}) {
  const Icon =
    id === "database"
      ? Database
      : id === "prediction"
        ? TrendingUp
        : id === "knowledge"
          ? BookOpen
          : Sparkles;
  return (
    <span className={cn("agent-icon", `agent-${id}`, className)}>
      <Icon size={19} strokeWidth={1.7} />
    </span>
  );
}
