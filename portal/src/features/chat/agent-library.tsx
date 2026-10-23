// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useState } from "react";
import { Search } from "lucide-react";
import { agentNames } from "@/components/agent-icon";
import { cn } from "@/lib/utils";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import type { Agent, AgentId } from "@/lib/types";

export function AgentLibrary({
  agents,
  loading,
  onSelect,
}: {
  agents: Agent[];
  loading: boolean;
  onSelect: (id: AgentId) => void;
}) {
  const [search, setSearch] = useState("");
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const visible = agents.filter((agent) =>
    (!onlyAvailable || agent.available) && `${agent.name} ${agent.description}`
      .toLocaleLowerCase("vi")
      .includes(search.toLocaleLowerCase("vi")),
  );
  return (
    <section className="agent-explorer">
      <div className="agent-explorer-inner">
        <h1>Agent</h1>
        <p className="agent-explorer-description">
          Những trợ lý chuyên biệt, cùng bạn giải quyết công việc.
        </p>
        <InputGroup className="agent-search">
          <InputGroupInput
            placeholder="Tìm agent theo tên hoặc công việc"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Tìm agent"
          />
          <InputGroupAddon><Search /></InputGroupAddon>
        </InputGroup>
        <div className="agent-library-toolbar">
          <Tabs value={onlyAvailable ? "available" : "all"} onValueChange={(value) => setOnlyAvailable(value === "available")}>
            <TabsList aria-label="Lọc agent">
              <TabsTrigger value="all">Tất cả</TabsTrigger>
              <TabsTrigger value="available">Sẵn sàng</TabsTrigger>
            </TabsList>
          </Tabs>
          <span>{visible.length} trợ lý</span>
        </div>
        <h2 className="agent-section-heading">Dành cho công việc của bạn</h2>
        <div className="agent-grid">
          {loading
            ? [0, 1, 2].map((key) => (
                <Skeleton key={key} className="h-28 rounded-xl" />
              ))
            : visible.map((agent) => (
                <div
                  key={agent.id}
                  data-slot="card"
                  role={agent.available ? "button" : undefined}
                  tabIndex={agent.available ? 0 : -1}
                  aria-label={`Bắt đầu trò chuyện với ${agentNames[agent.id] ?? agent.name}`}
                  onClick={() => agent.available && onSelect(agent.id)}
                  onKeyDown={(event) => {
                    if (agent.available && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      onSelect(agent.id);
                    }
                  }}
                  className={cn(
                    "agent-card group relative flex flex-col justify-start rounded-xl border border-border/80 bg-card p-4 transition-all duration-200",
                    agent.available
                      ? "cursor-pointer hover:border-primary/50 hover:bg-muted/20 hover:shadow-xs hover:-translate-y-0.5 active:translate-y-0"
                      : "opacity-60 cursor-not-allowed",
                  )}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <h3 className="text-sm font-semibold tracking-tight text-foreground group-hover:text-primary transition-colors">
                      {agentNames[agent.id] ?? agent.name}
                    </h3>
                    <span className={`agent-availability ${agent.available ? "is-ready" : ""}`}>
                      <i />
                      {agent.available ? "Sẵn sàng" : "Chưa sẵn sàng"}
                    </span>
                  </div>
                  <p className="agent-card-description">
                    {agent.description}
                  </p>
                </div>
              ))}
        </div>
        {!loading && !visible.length && (
          <p className="py-16 text-center text-sm text-muted-foreground">
            Không tìm thấy agent phù hợp.
          </p>
        )}
      </div>
    </section>
  );
}
