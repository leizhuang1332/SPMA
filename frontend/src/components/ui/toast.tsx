'use client';

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { slideDown } from '@/lib/animations';

interface Toast {
  id: number;
  message: string;
  type: 'error' | 'warning' | 'info';
  action?: { label: string; onClick: () => void };
}

let toastId = 0;
const listeners = new Set<(toast: Toast) => void>();

export function showToast(
  message: string,
  type: Toast['type'] = 'info',
  action?: Toast['action'],
) {
  const toast: Toast = { id: ++toastId, message, type, action };
  listeners.forEach(fn => fn(toast));
}

export default function ErrorToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const handler = (toast: Toast) => {
      setToasts(prev => [...prev, toast]);
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== toast.id));
      }, 5000);
    };
    listeners.add(handler);
    return () => { listeners.delete(handler); };
  }, []);

  const textColor = {
    error: 'var(--danger)',
    warning: 'var(--warning)',
    info: 'var(--primary)',
  };

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 flex flex-col gap-2">
      <AnimatePresence>
        {toasts.map(t => (
          <motion.div
            key={t.id}
            variants={slideDown}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="px-4 py-2.5 rounded-lg border text-[12px] bg-[var(--card)] shadow-lg max-w-md flex items-center gap-2"
            style={{
              borderColor: textColor[t.type],
              color: textColor[t.type],
            }}
          >
            <span>{t.message}</span>
            {t.action && (
              <button
                onClick={t.action.onClick}
                className="ml-3 underline hover:opacity-80 font-medium whitespace-nowrap"
              >
                {t.action.label}
              </button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
