from report_skill.orchestrator import normalize_report
from report_skill import schemas, tier

snap = schemas.load()
tier.set_tier("W", source="test")

template = {"schema": {"blocks": [
    {"id": "broken_table", "type": "table", "props": {
        "columns": [
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "level", "label": "Level", "type": "select", "options": ["A","B","C"], "required": True},
        ]
    }}
]}}
draft = {"broken_table": "just plain prose, not a table at all"}
result = normalize_report(template, draft, snap)
print("blocks:")
for b in result.blocks:
    detail = (b.detail or "")[:80]
    print(f"  {b.status:>10} {b.block_id}  -- {detail}")
print("extra_blocks:", result.extra_blocks)
print("content keys:", list(result.content))
