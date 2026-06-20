'use client';

import { useEffect, useRef, RefObject, useCallback } from 'react';

export function useAutoScroll(
  containerRef: RefObject<HTMLDivElement | null>,
  deps: unknown[],
) {
  // Store deps in a ref so the effect always has the latest values
  const depsRef = useRef(deps);
  depsRef.current = deps;

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (isNearBottom || container.scrollTop === 0) {
      container.scrollTop = container.scrollHeight;
    }
  }, [containerRef]);

  useEffect(() => {
    scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
