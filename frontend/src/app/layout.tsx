import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SPMA — 智能问答",
  description: "企业级多源 RAG 智能问答系统",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="antialiased">
        {children}
      </body>
    </html>
  );
}
