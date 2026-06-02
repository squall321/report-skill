---
name: bridge-process
description: Fulfill pending report-skill LLM bridge requests. Use when the user says "process bridge queue", "drain bridge", "handle pending llm requests", "응답 처리해줘" while a `report from-prompt` / `adhoc` / `llm generate-block` command is waiting. Reads `.req.json` files in the report-skill bridge cache dir (typically `%LOCALAPPDATA%\report-skill\.skill-cache\bridge\`), generates the LLM response Claude would have produced, writes `.resp.txt` so the CLI un-blocks.
---

# /bridge-process — be the LLM for `report-skill`

The user has invoked `report-skill report from-prompt` / `report adhoc` / `llm generate-block` with `SKILL_LLM_PROVIDER=bridge` set. The CLI is currently BLOCKED, polling the report-skill bridge cache dir (typically `%LOCALAPPDATA%\report-skill\.skill-cache\bridge\`) for a response file. Your job: be the LLM. Read each pending request, generate the right response, write it to disk.

## Loop

Repeat until the directory has no `*.req.json` files:

### 1. List pending requests

Resolve the bridge cache dir first (run `report-skill bridge dir` if available, otherwise it is `%LOCALAPPDATA%\report-skill\.skill-cache\bridge\` on standalone installs and `<repo>\.skill-cache\bridge\` on source checkouts). Glob `<bridge-dir>\*.req.json` and sort by mtime (oldest first).

### 2. Read one request

Each `<req_id>.req.json` looks like:

```json
{
  "id": "abc123def456",
  "created_at": "2026-05-27T...",
  "messages": [
    {"role": "system", "content": "You are a content extractor..."},
    {"role": "user", "content": "User wrote: \"\"\"...\"\"\" \nFill the `summary` block (widget=rich_text). Return JSON: {\"markdown\": \"...\"}"}
  ],
  "max_tokens": 800,
  "temperature": 0.1,
  "json_mode": true,
  "response_path": "<bridge-dir>\\abc123def456.resp.txt"
}
```

### 3. Generate the response

Read `messages` carefully. The system message defines the role; the user message contains the task + schema constraints. Generate the response YOU would give if you were the LLM being prompted:

- If `json_mode` is true OR the user message demands JSON → output ONLY valid JSON, no prose around it, no markdown code fences.
- If the task is "fill block X of type Y", produce content matching the widget's content_schema (the prompt includes the schema or describes its shape).
- Keep it terse — these are block-by-block prompts. One block = one tight JSON object.
- Korean and English both fine — follow the user's language.

### 4. Write the response file

Write your raw response (NOT wrapped in markdown, NOT prefixed with "Here's the JSON:") to the path in `response_path` (i.e. `<req_id>.resp.txt`). The CLI is polling that exact filename — anything else is invisible to it.

Use the Write tool. Don't write a `.json` file even when the content IS json — the bridge contract is `.resp.txt`. The skill side handles parsing.

### 5. Confirm and repeat

Note that you wrote the file, then check the bridge dir again for more pending requests. The `.req.json` file gets deleted by the CLI once it picks up the response — don't manually delete it.

## End-of-loop

When `glob *.req.json` returns nothing, tell the user "bridge queue drained — N response(s) written" and stop.

## Edge cases

- **Empty messages** — write `{"error": "empty prompt"}` and continue.
- **Schema validation hints in prompt** — respect them. The skill validates your response with jsonschema; failing that triggers a retry which sends a new request including the prior error. Treat retries seriously — fix what the error says, leave the rest alone.
- **No bridge dir** — say "no pending requests" and stop.
- **Old/stale `.req.json`** files older than 1 hour with no matching `.resp.txt` — these are abandoned. Skip them but mention them in the final summary.

## Don't

- Don't add markdown fences around JSON responses (`json ... `)
- Don't comment on what you're doing in the response file
- Don't write to `.req.json` (read-only on your side)
- Don't try to validate against the schema yourself — that's the skill's job
- Don't process requests faster than the user can review — process them one at a time, write the file, move on
