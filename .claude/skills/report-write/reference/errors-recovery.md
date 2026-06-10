# report-write reference — typed errors + recovery flows

Referenced from SKILL.md. Every MCP/CLI write can return a typed `{error: <code>, ...}` payload (v0.5.2+, extended in v0.13.0). Find the code in the table, follow the matching reaction; for the 409/403 families with a numbered "Recovery flow", follow that worked scenario instead of improvising.

## report_lock_status — inspect the edit-lock holder

```
report_lock_status(report_id=<id>)
→ {
    "report_id": 412,
    "locked": true,
    "holder": {"user_id": 7, "user_name": "홍길동", "user_email": "hong@ex.com",
                "acquired_at": "2026-06-05T08:55:11Z", "expires_at": "2026-06-05T09:25:11Z"},
    "self_held": false,
    "reason": "weekly_review"
  }
```

Call before any write operation when the user mentions "잠겨있다 / 누가 편집 중" or after seeing an `author_locked` / `lock_held_by_other` error. When `self_held=true`, the current actor already owns the lock and can keep writing. When `locked=true` AND `self_held=false`, surface the holder name + expiry to the user and STOP — do not retry.

## AuthorLockedError surfacing

When the report's author has set a manual edit lock ("작성자가 수정 잠금 상태입니다") any write tool returns:

```json
{"error": "author_locked", "reason": "<lock 사유>", "report_id": 412}
```

LLM behaviour:

- Do NOT retry. The lock is intentional and human-set; immediate retry will fail identically.
- Tell the user the lock reason and that only the author (or a system admin force-unset) can release it.
- Offer to call `report_lock_status` to confirm the current holder + ETA, or to wait for unlock.
- For a different report, the lock is irrelevant — proceed normally.

## Typed error codes (v0.5.2 + v0.13.0)

The MCP server maps the backend's stable error signatures to typed `{error: <code>, ...}` payloads instead of generic `"API error"`. Use the table below to decide how to react:

| code                          | status | meaning                                                                                  | how the LLM should react                                                                                          |
|-------------------------------|--------|------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `author_locked`               | 403    | author has set a manual lock on the report                                               | stop, surface `reason` + `report_id`, do not retry; suggest `report_lock_status` or wait                          |
| `lock_held_by_other`          | 409    | another user is actively editing (system edit-lock, time-bound)                          | surface holder, wait or retry after expiry; do not force-unlock — see Recovery flow #3                            |
| `lock_not_held`               | 409    | tried to release/extend a lock you don't hold                                            | call `report_lock_status` to reconcile state; usually a stale client                                              |
| `revision_mismatch`           | 409    | someone PATCHed the report since you fetched it                                          | auto-retry up to `--max-retries` (default 3); the skill re-fetches + re-merges; on final failure see Recovery flow #1 |
| `composite_revision_mismatch` | 409    | composite items[] was edited concurrently                                                | re-fetch composite via `composite_get`, re-build items list, retry — see Recovery flow #2                         |
| `finalized_readonly`          | 403    | report is `phase=finalized` — body PATCH is blocked                                      | suggest `report_unpublish` first if the user really wants to edit; otherwise stop — see Recovery flow #4          |
| `no_edit_permission`          | 403    | actor is not the author / coauthor / board-default editor                                | stop and surface — mount edit-policy or coauthor list controls this; not retryable                                |
| `out_of_workspace_scope`      | 403    | actor's workspace tree does not cover the target report / composite                      | stop; resource is invisible to this actor — do not retry under a different workspace slug                         |
| `share_setup_forbidden`       | 403    | only the content owner / sys admin may add/remove a content-level grant (RA dbdbf99)     | stop and surface — `content_share_add` / `_remove` requires owner. service account usually cannot do this         |
| `board_share_forbidden`       | 403    | only the board manager / sys admin may add/remove a board or folder grant (RA dbdbf99)   | stop and surface — `board_share_*` / `folder_share_*` requires manager rights on the target board                  |
| `trash_restore_forbidden`     | 403    | only the report owner / sys admin may trash or restore a report (RA dc8bd45)             | stop and surface — non-owners cannot soft-delete; mention the actual owner if known                               |
| `takedown_owner_forbidden`    | 403    | only the report owner may submit a takedown request on their own report (RA 3e92860)     | stop and surface — non-owners cannot file takedown requests; ask the actual owner                                  |
| `takedown_manager_forbidden`  | 403    | only the target board's manager / sys admin may approve / reject a takedown (RA 3e92860) | stop and surface — service account usually lacks this; do not retry                                                |
| `takedown_already_processed`  | 403    | the takedown request was already approved / rejected / withdrawn (RA 3e92860)            | stop — DO NOT retry. Re-fetch via `takedowns_list` to confirm the final status                                     |
| `mount_forbidden`             | 403    | board-manager-only mount op (e.g. unmount) attempted by a non-manager (v0.13.0)          | use `report_takedown_request` instead — owners cannot unmount manager-controlled boards                            |
| `mount_target_invalid`        | 400    | bad workspace slug / non-org workspace / folder mismatch on a mount op (v0.13.0)         | fix the target first: resolve the slug via `workspaces_list --kind org` and the folder via `folders_list`; do not retry as-is |
| `report_still_mounted`        | 409    | PERMANENT delete (`report_delete`) attempted while the report is still mounted (v0.13.0) | → see Recovery flow #5                                                                                             |
| `network_unreachable`         | n/a    | RA backend unreachable (connection refused / DNS / timeout) (v0.13.0)                    | retryable — the backend looks down; tell the user and retry later; this is NOT a code bug                          |
| `auth_unavailable`            | n/a    | login failed because the auth endpoint could not be reached (v0.13.0)                    | retryable — the backend looks down; tell the user and retry later; this is NOT a code bug                          |
| `snapshot_missing`            | n/a    | local widget catalog snapshot not present                                                | run `report-skill catalog sync` (CLI) or the `catalog_sync` MCP tool once, then retry                              |
| `llm_error`                   | n/a    | configured LLM provider returned an error during `from-prompt` / `revise`                | surface `detail`; try `--provider <other>` or set `SKILL_LLM_PROVIDER`                                            |
| `no_llm_provider`             | n/a    | no LLM provider configured for a tool that needs one                                     | tell the user to set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / use `bridge` mode                                   |

For all other ApiErrors the legacy `{error: "API error", status_code, message, payload}` shape still applies.

## Recovery flows (worked scenarios)

When a typed error from the table above fires, follow the matching numbered flow instead of improvising. All tool names below are verified against the runtime `_DISPATCH` map.

### Flow #1 — `revision_mismatch` (auto-retry exhausted)

1. `report_show` (or `report_outline` + `page_show_content`) to re-fetch latest body + revision.
2. Re-apply YOUR edit intent on top of the other writer's changes — never blind-overwrite.
3. Re-call `report_update`. For `report_append`, raise `max_retries` (default 3).
4. Still failing → `report_activities` to identify the concurrent editor, report to the user, stop.

### Flow #2 — `composite_revision_mismatch`

1. `composite_get` for latest revision + items[].
2. Re-apply your change (add/remove/reorder) on the fresh items list.
3. Re-call `composite_items_set` (items) or `composite_update` (top-level) with the NEW expected_revision.
4. Two consecutive failures → concurrent editing in progress; report and stop.

### Flow #3 — `lock_held_by_other`

1. `report_lock_status` → check holder + expiry.
2. If the lock is yours, simply retry the write.
3. If held by someone else: show holder + expiry to the user; do NOT hammer retries.
4. If the user opts to wait: retry ONCE after expiry; on failure go back to step 1.
5. NEVER attempt force-unlock.

### Flow #4 — `finalized_readonly` (edit a published report)

1. `report_show` to confirm phase=finalized; ask the user to confirm "unpublish → edit → republish".
2. `report_unpublish` (idempotent) — drops to drafting.
3. Edit via `report_update` / `report_add_page` / `report_revise`.
4. `report_publish` — idempotent; notifications fire only on the actual transition.

Note: composite summary widgets and mount/folder ops are NOT blocked by finalize — no flow needed there.

### Flow #5 — delete a mounted report (409 `report_still_mounted`)

- If the goal is soft delete: just call `report_trash` — no unmount needed (board copies are preserved). A 403 here is `trash_restore_forbidden` (owner-only).
- If the goal is PERMANENT delete (`report_delete`) and it returns 409 report_still_mounted:

1. `report_mounts` to enumerate every mounted workspace_slug.
2. For each: `report_unmount` if you are that board's manager; otherwise `report_takedown_request` (owner) and wait for the manager's `takedown_approve`.
3. Once mounts are 0, re-call `report_delete` with confirm=true.
4. BEFORE deleting, surface `composite_ref_count` from `report_show` to the user — those composite agenda items disappear with the report.

## Network resilience (v0.13.0)

POST/PATCH timeouts: the write may have committed server-side even though the response was lost. Before re-issuing a create, check existence via `reports_search` / `reports_list` to avoid duplicates. `network_unreachable` / `auth_unavailable` codes are retryable — the backend is down, not the skill.

## Trashed-report guard (v0.13.0)

Mounting or publishing a trashed report is rejected by the skill — restore first via `report_restore`, then mount/publish.

## Lifecycle notes (v0.5.2)

- **phase=finalized self-lock** — direct body PATCH (`report_update`, `report_add_page`, `report_revise`, `report_append`) on a `phase=finalized` report is rejected with `finalized_readonly`. The skill surfaces this in the response `warnings` list when applicable; for any intentional edit, call `report_unpublish` first to drop the report back to `drafting`, then patch, then `report_publish` again. Composite **summary widgets** and mount/folder operations are not blocked by finalize.
- **mount auto-transitions `drafting` → `reviewing`** — calling `report_mount` on a `drafting` report automatically advances `phase` to `reviewing` (one-way). Subsequent unmounts do not revert. If the user later wants the report back at `drafting`, call `report_unpublish` (no-op on non-finalized) or manually set `phase` via `report_update`.
- **`report_publish` is idempotent** — calling on an already-finalized report is a no-op that returns current state. Notification fan-out (`report.phase_to_finalized`) only fires on the actual transition, not on idempotent re-calls. Same for `report_unpublish` on an already-drafting report.
