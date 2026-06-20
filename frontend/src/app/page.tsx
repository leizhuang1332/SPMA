export default function HomePage() {
  return (
    <main className="flex h-screen items-center justify-center bg-[var(--background)]">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-[10px] bg-[var(--primary)] text-white text-xl font-bold">
          S
        </div>
        <h1 className="text-lg font-semibold text-[var(--foreground)]">SPMA</h1>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">智能问答系统</p>
      </div>
    </main>
  );
}
