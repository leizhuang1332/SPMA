import type { Variants } from 'framer-motion';

export const slideUp: Variants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.3, ease: 'easeOut' },
  },
};

export const slideDown: Variants = {
  hidden: { opacity: 0, y: -10 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.3, ease: 'easeOut' },
  },
  exit: { opacity: 0, y: -10, transition: { duration: 0.3 } },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.2 } },
};

export const crossfade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.3 } },
  exit: { opacity: 0, transition: { duration: 0.3 } },
};

export const staggerChildren = {
  visible: {
    transition: { staggerChildren: 0.05 },
  },
};

export const bounceTap = {
  scale: 1,
  transition: { type: 'spring', stiffness: 400, damping: 10 },
};

export const soundWave: Variants = {
  animate: {
    scaleY: [0.5, 1, 0.5],
    transition: { duration: 0.6, repeat: Infinity, ease: 'easeInOut' },
  },
};

export const progressFill = (width: string) => ({
  width,
  transition: { duration: 0.5, ease: 'easeOut' },
});
