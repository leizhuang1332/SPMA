# Lazy Session Creation & Empty History Handling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lazy-persist sessions on first query (not on "+" click) + return empty turns instead of 404 for sessions with no history.

**Architecture:** Client-side UUID generation replaces POST /sessions on "+" click. Both /query and /query/stream auto-create sessions on first message with title = query[:10]. /history distinguishes "no checkpoints" (200 empty) from "not found" (404).

**Tech Stack:** Next.js 14, FastAPI/Python, asyncpg

---

### Task 1: Backend — /sessions/{id}/history returns 200 for empty history

**Files:**
- Modify: `src/spma/api/routes/session.py:98-117`
- Test: existing `tests/test_extract_turns.py`

- [ ] **Step 1: Add `store` dependency to `get_session_history` signature**

Change the function signature at line 98-102 from:
```python
@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
```
To:
```python
@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: SessionStore = Depends(get_session_store),
):
```

- [ ] **Step 2: Replace 404 for empty history with 200 empty array**

Replace lines 114-116:
```python
    result = await extract_turns(session_id, checkpointer, limit, offset)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found or has no history")
    return result
```
With:
```python
    result = await extract_turns(session_id, checkpointer, limit, offset)
    if result is None:
        # Distinguish: session exists but has no checkpoints → 200 empty
        # vs session truly doesn't exist → 404
        if await store.session_exists(session_id):
            return {"turns": [], "total": 0, "offset": offset, "limit": limit}
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return result
```

- [ ] **Step 3: Run existing extract_turns tests to verify no regression**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/test_extract_turns.py -v`
Expected: all 11 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/spma/api/routes/session.py
git commit -m "fix: return 200 with empty turns instead of 404 for sessions with no history"
```

---

### Task 2: Backend — /query/stream auto-creates session on first query

**Files:**
- Modify: `src/spma/api/routes/query.py:555-569`

- [ ] **Step 1: Replace session_exists 404 with auto-create**

Replace lines 556-559:
```python
    # Route-level validation: fail fast before generator
    store = get_session_store()
    if not await store.session_exists(req.session_id):
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")
```
With:
```python
    # Route-level: ensure session exists (lazy-create on first query)
    store = get_session_store()
    if not await store.session_exists(req.session_id):
        title = req.query[:10] if req.query else None
        await store.create_session(title=title)
```

- [ ] **Step 2: Change title auto-assignment from 50 to 10 chars**

Replace line 567:
```python
            await store.update_session_title(req.session_id, req.query[:50])
```
With:
```python
            await store.update_session_title(req.session_id, req.query[:10])
```

- [ ] **Step 3: Run streaming tests to verify**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/integration/test_streaming.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/spma/api/routes/query.py
git commit -m "feat: auto-create session on first /query/stream call with 10-char title"
```

---

### Task 3: Backend — /query adds title param to create_session + 10-char limit

**Files:**
- Modify: `src/spma/api/routes/query.py:63-68`

- [ ] **Step 1: Add title param to create_session call**

Replace line 64:
```python
                await store.create_session()
```
With:
```python
                title = req.query[:10] if req.query else None
                await store.create_session(title=title)
```

- [ ] **Step 2: Change title auto-assignment from 50 to 10 chars**

Replace line 68:
```python
                await store.update_session_title(req.session_id, req.query[:50])
```
With:
```python
                await store.update_session_title(req.session_id, req.query[:10])
```

- [ ] **Step 3: Run relevant tests**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/integration/test_agent_loop.py tests/unit/api/ -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/spma/api/routes/query.py
git commit -m "fix: use 10-char title limit and pass title on auto-create in /query"
```

---

### Task 4: Frontend — Sidebar "+" button uses client-side UUID, no API calls

**Files:**
- Modify: `frontend/src/components/layout/sidebar.tsx:65-78`

- [ ] **Step 1: Replace handleNewSession to use client-side UUID + placeholder**

Replace lines 65-78:
```typescript
  const handleNewSession = async () => {
    dispatch({ type: 'RESET_QUERY' });
    try {
      const { session_id } = await api.createSession();
      // 立即获取完整 SessionRecord 并加入列表，确保侧边栏实时显示
      const session = await api.getSession(session_id);
      dispatch({ type: 'ADD_SESSION', session });
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId: session_id });
      window.history.pushState(null, '', `/chat/${session_id}`);
    } catch {
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId: null });
      window.history.pushState(null, '', '/');
    }
  };
```
With:
```typescript
  const handleNewSession = () => {
    dispatch({ type: 'RESET_QUERY' });
    const session_id = crypto.randomUUID();
    const now = new Date().toISOString();
    const placeholder: import('@/types/api').SessionRecord = {
      session_id,
      turns: [],
      created_at: now,
      updated_at: now,
    };
    dispatch({ type: 'ADD_SESSION', placeholder });
    dispatch({ type: 'SET_CURRENT_SESSION', sessionId: session_id });
    window.history.pushState(null, '', `/chat/${session_id}`);
  };
```

Note: `handleNewSession` changes from `async` to synchronous — remove the `async` keyword.

- [ ] **Step 2: TypeScript type check**

Run: `cd /Users/Ray/TraeProjects/SPMA/frontend && npx tsc --noEmit`
Expected: zero errors

- [ ] **Step 3: Build check**

Run: `cd /Users/Ray/TraeProjects/SPMA/frontend && npm run build`
Expected: successful build

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/sidebar.tsx
git commit -m "feat: use client-side UUID for new sessions, no API call on '+' click"
```

---

### Task 5: End-to-end manual verification

- [ ] **Step 1: Start dev servers and test all scenarios**

Scenario A — Empty history doesn't break rendering:
1. Open the app, send a message in an existing session → session A has history
2. Click "+" → new empty session B created (no API call)
3. Refresh page on session B
4. Verify: sidebar shows session A (from DB), session B is gone (not in DB yet), no error

Scenario B — Lazy creation on first message:
1. Click "+" → observe Network tab → zero API calls
2. Type a message "帮我分析一下REQ-187的风险" → send
3. Verify: Network tab shows POST /query/stream with 200
4. Refresh page → verify: new session appears in sidebar with title "帮我分析一下RE"

Scenario C — Refresh clears unsent:
1. Click "+" → session appears in sidebar
2. Refresh page (Cmd+R) → session disappears (not in DB)

Scenario D — /query non-streaming path:
1. If there's a way to trigger the non-streaming /query endpoint, verify it also auto-creates sessions

- [ ] **Step 5: Commit verification notes if any issues found**

---

## Self-Review Checklist

- [x] Spec coverage: Each spec requirement maps to a task
  - Empty history → 200: Task 1
  - Lazy creation on /query/stream: Task 2
  - Lazy creation on /query: Task 3
  - Client-side UUID "+" button: Task 4
- [x] No placeholders: All code is complete and exact
- [x] Type consistency: `SessionRecord` type correctly imported in Task 4, `SessionStore` correctly used in Task 1
- [x] Edge cases covered: Task 5 verification covers all scenarios from spec
