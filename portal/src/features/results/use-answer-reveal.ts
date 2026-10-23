// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useEffect, useState } from "react";

/**
 * Adaptive typewriter reveal animation that progressively outputs text
 * character-by-character / word-by-word like ChatGPT, complete with typing
 * state for displaying a blinking cursor.
 */
export function useAnswerReveal(
  answer: string,
  animate: boolean,
  customDuration?: number,
) {
  const [visible, setVisible] = useState(animate ? 0 : (answer?.length ?? 0));
  const [isTyping, setIsTyping] = useState(Boolean(animate && answer?.length));

  useEffect(() => {
    if (!animate || !answer || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(answer?.length ?? 0);
      setIsTyping(false);
      return;
    }

    setVisible(0);
    setIsTyping(true);

    const characters = Array.from(answer);
    const totalChars = characters.length;
    if (totalChars === 0) {
      setIsTyping(false);
      return;
    }

    // Pacing scaled to natural human / LLM reading speeds:
    // Short answers (100-300 chars): ~2 to 3.5s
    // Medium answers (500-1000 chars): ~3.5 to 5s
    // Long answers (>1500 chars): gracefully capped ~5.5s
    const duration =
      customDuration ?? Math.min(5500, Math.max(1400, totalChars * 18));

    let start: number | undefined;
    let frame: number;

    const tick = (now: number) => {
      start ??= now;
      const elapsed = now - start;
      const progress = Math.min(1, elapsed / duration);
      const count = Math.min(totalChars, Math.max(1, Math.floor(progress * totalChars)));

      setVisible(count);

      if (count < totalChars) {
        frame = requestAnimationFrame(tick);
      } else {
        setIsTyping(false);
      }
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [answer, animate, customDuration]);

  return {
    text: animate ? answer.slice(0, visible) : answer,
    isTyping,
  };
}
