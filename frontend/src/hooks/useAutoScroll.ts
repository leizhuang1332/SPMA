'use client';

import { useEffect, RefObject } from 'react';

export function useAutoScroll(
  containerRef: RefObject<HTMLDivElement | null>,
  deps: unknown[],
) {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Only auto-scroll if user is near the bottom (within 150px)
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (isNearBottom || container.scrollTop === 0) {
      container.scrollTop = container.scrollHeight;
    }
  }, deps);
}
