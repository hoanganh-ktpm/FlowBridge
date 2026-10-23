// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { Component, type ReactNode } from "react";

export class PresentationBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (
      <p role="status" className="text-sm text-muted-foreground my-3">
        Chưa hiển thị được bảng hoặc biểu đồ này. Bạn vẫn có thể đọc nội dung trả lời.
      </p>
    ) : this.props.children;
  }
}
