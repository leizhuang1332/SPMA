# Lazy Session Creation & Empty History Handling

**Date:** 2026-06-21
**Status:** Approved

## Context

Two issues with the current session management:

1. **Empty history breaks rendering:** `GET /api/v1/sessions/{id}/history` returns 404 when a session has no conversation history (no LangGraph checkpoints), causing the frontend to enter an error path that can interfere with other sessions' rendering.

2. **Eager DB insertion:** Clicking "+" immediately calls `POST /sessions` and inserts a row into the database. Most of these sessions remain empty (user never sends a message), polluting the database.

## Goals

1. A session with no conversation history must not affect the rendering of other sessions
2. Sessions should only be persisted to the database when the user sends their first message
3. The session title should be auto-generated from the first 10 characters of the first query

## Design

### Architecture Overview

```
Click "+" → client-side UUID → local placeholder → navigate
                         (zero API calls)

First message → POST /query/stream (or /query)
             → backend detects session doesn't exist
             → INSERT INTO sessions (title = query[:10])
             → normal LangGraph execution

Refresh → GET /sessions → session now appears
       → GET /sessions/{id}/history → 200 with turns
```

Empty sessions (created via "+" but never used) disappear on refresh since they only exist in client-side state.

### Changes

#### 1. Frontend: Sidebar "+" button

**File:** `frontend/src/components/layout/sidebar.tsx`

Replace the `handleNewSession` implementation:
- Remove `api.createSession()` and `api.getSession()` calls
- Use `crypto.randomUUID()` to generate session ID client-side
- Construct a local `SessionRecord` placeholder with `turns: []`
- Dispatch `ADD_SESSION` + `SET_CURRENT_SESSION`
- Navigate to `/chat/{uuid}` via `window.history.pushState`

#### 2. Backend: `/api/v1/sessions/{session_id}/history`

**File:** `src/spma/api/routes/session.py`

When `extract_turns()` returns `None`:
- Check `store.session_exists(session_id)`
- If session exists but has no checkpoints → return `200 { turns: [], total: 0, offset, limit }`
- If session does not exist → return 404 (unchanged)

This prevents the 404 error path for valid sessions that simply haven't been used yet, while preserving the 404 for truly invalid session IDs.

#### 3. Backend: `/api/v1/query/stream`

**File:** `src/spma/api/routes/query.py` (lines ~557-567)

- Replace `session_exists` → 404 with auto-create:
  ```python
  if not await store.session_exists(req.session_id):
      await store.create_session(title=req.query[:10], user_id=user_id)
  ```
- Change title auto-assignment from `req.query[:50]` to `req.query[:10]`
- The existing auto-title logic (for sessions that exist but lack a title) keeps its new 10-char limit

#### 4. Backend: `/api/v1/query`

**File:** `src/spma/api/routes/query.py` (lines ~63-68)

This endpoint already implements lazy creation. Changes:
- `create_session()` call: add `title=req.query[:10]` parameter
- `update_session_title()` call: change `req.query[:50]` to `req.query[:10]`

### Edge Cases

| Scenario | Behavior |
|---|---|
| Click "+", send message | Session created in DB with auto-title. Normal flow. |
| Click "+", refresh before sending | Session disappears. Not in DB, placeholder lost on reload. |
| Direct URL `/chat/{random-uuid}` | Both `/history` and `/session/{id}` return 404. Frontend shows empty chat. First message triggers auto-create. |
| Session exists, has title | Title never overwritten. Only set on creation or when NULL. |
| Session exists, no checkpoints | `/history` returns 200 with empty turns. No error. |

### Files Modified

| File | Change Summary |
|---|---|
| `frontend/src/components/layout/sidebar.tsx` | `handleNewSession`: client-side UUID + placeholder, no API calls |
| `src/spma/api/routes/session.py` | `/sessions/{id}/history`: distinguish "no history" (200) from "not found" (404) |
| `src/spma/api/routes/query.py` | `/query/stream`: auto-create session on first query + 10-char title |
| `src/spma/api/routes/query.py` | `/query`: add title param to create_session + 10-char title |

### Verification

1. **Empty history doesn't block rendering:**
   - Create session A, send a message → session A has history
   - Create session B, DON'T send a message
   - Refresh on session B → sidebar shows session A ✅, session B not broken ✅

2. **Lazy creation:**
   - Click "+" → check DevTools Network tab → zero API calls ✅
   - Send first message → check `sessions` table → new row with `title` = first 10 chars ✅
   - Refresh → session appears in sidebar ✅

3. **Refresh clears unused:**
   - Click "+" → refresh immediately → session gone from sidebar ✅

4. **Backward compatibility:**
   - `POST /sessions` endpoint still works for API consumers
   - Existing sessions with titles are not affected
