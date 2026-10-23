// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { Search, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Conversation } from "@/lib/types";

export function ConversationSearch({
  open,
  onOpenChange,
  search,
  setSearch,
  conversations,
  disabled,
  onSelect,
  onNew,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  search: string;
  setSearch: (value: string) => void;
  conversations: Conversation[];
  disabled: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const visible = conversations.filter((item) =>
    item.title.toLocaleLowerCase("vi").includes(search.toLocaleLowerCase("vi")),
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="conversation-search gap-0 overflow-hidden p-0 sm:max-w-lg">
        <DialogTitle className="sr-only">Tìm hội thoại</DialogTitle>
        <DialogDescription className="sr-only">
          Tìm và mở lại cuộc trò chuyện của bạn.
        </DialogDescription>
        <InputGroup className="h-12 rounded-none border-0 border-b pr-10 shadow-none">
          <InputGroupInput
            autoFocus
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Tìm hội thoại"
            placeholder="Tìm trong các cuộc trò chuyện"
            className="text-sm"
          />
          <InputGroupAddon><Search /></InputGroupAddon>
        </InputGroup>
        <div className="max-h-[55svh] min-h-52 overflow-y-auto p-2">
          {!search && (
            <Button
              variant="ghost"
              className="search-result-row"
              disabled={disabled}
              onClick={() => {
                onNew();
                onOpenChange(false);
              }}
            >
              <SquarePen className="size-[18px]" />
              Cuộc trò chuyện mới
            </Button>
          )}
          <p className="px-3 pb-2 pt-4 text-xs font-medium text-muted-foreground">
            {search ? "Kết quả tìm kiếm" : "Gần đây"}
          </p>
          {visible.map((item) => (
            <Button
              key={item.id}
              variant="ghost"
              className="search-result-row font-normal"
              disabled={disabled}
              onClick={() => {
                onSelect(item.id);
                onOpenChange(false);
              }}
            >
              <span className="truncate">{item.title}</span>
            </Button>
          ))}
          {!visible.length && (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">
              {search
                ? "Không tìm thấy cuộc trò chuyện phù hợp."
                : "Chưa có cuộc trò chuyện."}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
