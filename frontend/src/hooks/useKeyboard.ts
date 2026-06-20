'use client';

import { useEffect } from 'react';

type KeyHandler = (e: KeyboardEvent) => void;

interface Shortcut {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  handler: KeyHandler;
}

export function useKeyboard(shortcuts: Shortcut[]) {
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      for (const s of shortcuts) {
        const keyMatch = e.key.toLowerCase() === s.key.toLowerCase();
        const metaMatch = s.metaKey ? (e.metaKey || e.ctrlKey) : true;
        const ctrlMatch = s.ctrlKey ? e.ctrlKey : true;
        const shiftMatch = s.shiftKey !== undefined ? e.shiftKey === s.shiftKey : true;

        if (keyMatch && metaMatch && ctrlMatch && shiftMatch) {
          e.preventDefault();
          s.handler(e);
          return;
        }
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [shortcuts]);
}

export function getModifierKey(): string {
  if (typeof navigator === 'undefined') return 'Ctrl';
  return navigator.platform?.includes('Mac') ? '⌘' : 'Ctrl';
}
