import type { Metadata } from 'next';
import { ThemeProvider } from 'next-themes';
import { AppProvider } from '@/context/app-context';
import './globals.css';

export const metadata: Metadata = {
  title: 'SPMA — 智能问答',
  description: '企业级多源 RAG 智能问答系统',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <AppProvider>
            {children}
          </AppProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
