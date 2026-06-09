# Building a Claude Skill — report-skill 제작 가이드

이 문서는 ReportArchive를 LLM(클로드)이 직접 조작할 수 있게 만든 외부 스킬 레이어 `report-skill`이 어떻게 만들어졌고, 같은 패턴으로 다른 시스템(예: 다른 사내 웹앱)에 맞는 클로드 스킬을 만들려면 무엇을 해야 하는지 정리한 메소드 가이드입니다.

소스 시스템을 한 줄도 건드리지 않고, HTTP 경계만으로 LLM-쓰기 가능한 표면(write surface)을 외부에 노출하는 패턴 — 이게 이 스킬의 핵심 설계 결정입니다.

---

## 1. 클로드 스킬이란

클로드가 한 외부 시스템에 "쓰기 행위"를 하려면 단순한 자연어로는 부족합니다. 시스템의 위젯/모델/엔드포인트가 요구하는 정확한 JSON 모양, id, 워크플로 순서를 LLM이 알아야 하기 때문입니다.

스킬은 그 간극을 메우는 4가지 표면을 한 묶음으로 제공합니다.

| 표면 | 용도 | 누가 읽나 |
|---|---|---|
| `SKILL.md` | 스킬 사용법·플로우·룰을 자연어로 명세 | LLM이 system context로 읽음 |
| MCP 도구 | 도구 호출 + 입력 스키마 + 디스패처 | LLM이 tool_use로 호출 |
| CLI | 같은 도구를 사람/스크립트가 호출 | 사용자·테스트·디버그 |
| HTTP 클라이언트 | 실제 외부 API 래퍼 | MCP·CLI가 공유 호출 |

LLM은 `SKILL.md`에서 "무엇을 할 수 있고 언제 어떤 순서로 호출하는가"를 배우고, MCP 도구로 실제 행위를 수행합니다. 같은 도구가 CLI로도 노출돼 있어 사람이 동일 동작을 명령행에서 재현·검증할 수 있습니다.

---

## 2. 아키텍처 — 8 계층

```
.claude/skills/report-write/SKILL.md   ← LLM이 읽는 명세서 (system context)
        ▲
        │  (LLM이 SKILL.md를 본 뒤 도구 선택)
        │
src/report_skill/
├── mcp_server.py        MCP stdio 서버 + _DISPATCH 76 tools (v0.9.x)
├── cli.py               Typer sub-apps (report / tools / mounts / composites / templates …)
├── client.py            httpx 동기 래퍼 — REST 호출 1:1
│
├── adapters/            위젯별 normalize() — LLM 입력 → 위젯 스키마
│   ├── rich_text.py     멘션 마크다운 → <a data-mention-*> HTML 변환
│   ├── table.py         rows + note + column_widths + merges 패스스루
│   ├── comparison.py    table + row_label_width
│   ├── image.py         files + caption + note
│   └── … 33개 위젯
│
├── prompt.py            _WIDGET_INPUT_HINTS + build_*_prompt() — LLM 프롬프트 빌더
├── report_builder.py    build_create_payload(_multi) — POST /reports 바디 생성
├── report_ops.py        update_blocks() + expected_revision/409 재시도
├── bundle.py            cross-instance dump/import — _TOP_KEEP 화이트리스트
│
└── data/
    ├── widgets.snapshot.json    번들된 위젯 카탈로그 (lambda 스키마 dump)
    └── templates/*.json         번들된 템플릿 스냅샷
```

각 계층은 위 계층의 입력을 받아 아래 계층으로 변환합니다.

- LLM은 `SKILL.md`만 보고 어떤 MCP 도구를 호출할지 결정합니다.
- MCP 도구는 `_DISPATCH` 디스패처로 라우팅돼 `ReportArchiveClient` 메서드를 호출합니다.
- 위젯 콘텐츠는 `adapters/`의 `normalize()`를 거쳐 외부 시스템이 요구하는 정확한 JSON으로 변환됩니다.
- 멀티-블록 보고서는 `report_builder.build_create_payload_multi()`가 모아서 `POST /api/reports`로 전송합니다.

---

## 3. 모든 capability의 3-view 패턴

이 스킬의 황금률: 외부 시스템의 새 기능 하나당 정확히 3개 표면이 동시에 추가됩니다.

```
새 endpoint POST /api/reports/{id}/copy
    │
    ├─ client.py:        copy_report(report_id, *, title, mode, folder_id) -> dict
    ├─ mcp_server.py:    _tool("report_copy", schema) + _do_report_copy() + _DISPATCH
    └─ cli.py:           @report_app.command("copy") def copy(...)
```

이 세 표면의 시그니처는 항상 일치해야 합니다.

- 같은 메서드명 prefix (`copy_report` ↔ `report_copy`)
- 같은 파라미터 이름·타입·기본값
- 같은 에러 처리 (httpx → `raise_for_status()` → 호출자가 처리)

이렇게 하면 SKILL.md 한 줄이 "내부 LLM 학습 자료(MCP)"와 "사람·CI가 실행하는 명령(CLI)" 양쪽 모두를 자동으로 커버합니다.

### MCP 도구 등록 패턴

```python
# mcp_server.py
_tool(
    "report_copy",
    "Copy an existing report to a personal folder.",
    {
        "type": "object",
        "required": ["report_id", "title"],
        "properties": {
            "report_id": {"type": "integer"},
            "title":     {"type": "string", "maxLength": 255},
            "mode":      {"type": "string", "enum": ["content", "full"]},
            "folder_id": {"type": "integer"},
        },
    },
)

def _do_report_copy(args: dict) -> Any:
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        return c.copy_report(
            rid,
            title=str(args["title"]),
            mode=args.get("mode", "full"),
            folder_id=args.get("folder_id"),
        )

_DISPATCH["report_copy"] = _do_report_copy
```

### CLI 미러 패턴

```python
# cli.py
@report_app.command("copy")
def report_copy(
    report_id: int,
    title: str = typer.Option(..., "--title"),
    mode: str = typer.Option("full", "--mode"),
    folder_id: int | None = typer.Option(None, "--folder-id"),
):
    """기존 보고서를 personal 폴더로 복사."""
    with ReportArchiveClient() as c:
        rich.print_json(data=c.copy_report(report_id, title=title, mode=mode, folder_id=folder_id))
```

---

## 4. 어댑터 패턴 — LLM 입력 정규화

어댑터는 LLM이 자유 형식으로 만든 위젯 콘텐츠를 외부 시스템의 정확한 스키마로 normalize 합니다. 33개 위젯이 모두 같은 패턴을 따릅니다.

```python
# adapters/table.py
class TableAdapter(WidgetAdapter):
    type = "table"

    def normalize(self, raw: Any) -> dict:
        if isinstance(raw, list):
            return {"rows": _coerce_rows(raw)}        # legacy: 리스트면 rows만
        if not isinstance(raw, dict):
            return {"rows": []}
        out = {"rows": _coerce_rows(raw.get("rows", []))}
        _apply_passthrough(raw, out)                  # note / column_widths / merges / table_width_px
        return out

def _clean_note(s: str | None) -> str | None:
    if not isinstance(s, str): return None
    s = s.lstrip()
    if s.startswith("※"): s = s[1:].lstrip()         # 렌더가 ※ 자동 부착 — 입력에 넣으면 중복
    s = s[:1000]
    return s or None
```

### 어댑터 설계 원칙

1. **화이트리스트 패스스루** — 외부 시스템 스키마에 정의된 필드만 통과시킨다. 모르는 필드는 조용히 버린다 (PATCH 시 400 방지).
2. **레거시 입력 관용** — LLM이 dict가 아닌 list를 줘도 동작하도록 (`isinstance` 분기). 새 필드 추가는 항상 옵션.
3. **무손실 round-trip** — fetch → adapter normalize → PATCH 가 같은 필드 집합을 유지해야 한다. 이게 깨지면 사용자가 수동 조정한 column_widths 같은 게 매 revise마다 사일런트 손실된다.
4. **placeholder + sentinel** — 마크다운 → HTML 변환처럼 nested 구조는 sentinel 문자(`\x00mention1\x00`)로 자리잡고 마지막에 한 번에 decode. recursion 시 closure로 placeholders 맵을 공유해야 nested 케이스가 깨지지 않는다 (v0.4.0의 mention nested in bold 버그가 이걸 놓쳤다가 수정됨).

---

## 5. 스키마 주도 프롬프트

LLM이 위젯 입력을 정확히 만들도록 두 가지를 함께 줍니다.

### (a) 위젯 카탈로그 스냅샷 (`data/widgets.snapshot.json`)

ReportArchive의 위젯 `content_schema`는 Python lambda여서 catalog API로 노출되지 않습니다. 대신 백엔드 venv를 read-only subprocess로 호출해 lambda를 실체화 → JSON Schema로 dump → 스킬 wheel에 번들합니다.

```powershell
report-skill catalog sync   # 백엔드 → snapshot 갱신
```

스냅샷에는 각 위젯의 `content_schema`(JSON Schema)가 들어 있어, LLM에게 위젯 입력 모양을 보여줄 때 그대로 활용됩니다.

### (b) 입력 힌트 (`_WIDGET_INPUT_HINTS`)

JSON Schema만으로는 부족한 인간-언어 가이드를 widget_type별로 1-3줄 추가합니다.

```python
# prompt.py
_WIDGET_INPUT_HINTS = {
    "table": (
        "rows is required. Optional: note (renders with ※ prefix automatically — do NOT include ※ yourself; "
        "max 1000 chars), column_widths (px ints), table_width_px, merges ({r,c,rs,cs})."
    ),
    "rich_text": (
        "Mentions use markdown-link syntax: [label](mention://report/<id>?ws=<slug>), "
        "[label](mention://dept/<slug>), [label](mention://entity/<id>?axis=<slug>). "
        "NEVER invent ids — resolve via reports_search / workspaces_list / entity_types_list / entities_list / report_types_list."
    ),
    # … 33개
}
```

프롬프트 빌더 (`build_create_prompt`, `build_block_revise_prompt`, `build_block_batch_prompt`)는 widget_type에 맞는 힌트를 JSON Schema 섹션 뒤에 자동으로 붙입니다.

### (c) 리졸버 도구

`id`가 필요한 곳(`workspace_slug`, `entity_id`, `report_id`)은 LLM이 절대 추측하지 않도록 리졸버 도구로 강제합니다.

| 리졸버 | 용도 |
|---|---|
| `reports_search` | 본문 mention://report/<id> 용 |
| `workspaces_list` | mention://dept/<slug> + collab_workspace_slugs |
| `entity_types_list` | 축 슬러그(model_name, customer_name 등) |
| `entities_list` | mention://entity/<id> + entity_ids |
| `report_types_list` | report_type_id |
| `folders_list` | mount/copy 시 folder_id |

룰: "NEVER invent ids" — SKILL.md에 명시되고 프롬프트 빌더 preamble에도 박힙니다.

---

## 6. 패키징 + 배포

### 두 가지 배포 방식

| 방식 | 대상 | 산출물 |
|---|---|---|
| **Wheel** (170 KB) | Python 있는 환경 | `report_skill-X.Y.Z-py3-none-any.whl` + `install.ps1` |
| **Standalone** (37 MB) | Python 없는 사내 PC | PyInstaller `--onedir --noupx --copy-metadata` 두 exe (`report-skill.exe`, `report-skill-mcp.exe`) + `install-standalone.ps1` |

### 빌드 스크립트

```powershell
.\scripts\build_release.ps1                # wheel zip 만
.\scripts\build_release.ps1 -WithExe       # wheel + standalone
.\scripts\build_release.ps1 -ExeOnly       # standalone만
.\scripts\build_release.ps1 -NoRefresh     # catalog sync 스킵
```

### PyInstaller 함정

- `--copy-metadata report-skill` 로 wheel METADATA를 exe에 베이크 — `importlib.metadata.version("report-skill")` 가 런타임에 이걸 읽음.
- 베이크 시점이 빌드 시점이라, wheel 버전이 0.4.0인 상태에서 standalone을 빌드하면 standalone이 0.4.0이라고 보고함. 0.5.0 wheel을 먼저 `pip install --force-reinstall` 한 뒤 standalone을 다시 빌드해야 함.
- `--noupx`: UPX 압축은 Windows Defender가 자주 오탐 → 끔.
- `--onedir`: `--onefile`보다 시작이 빠르고 stale dist-info 청소가 쉬움.

### Standalone 설치본 마이그레이션 함정

새 standalone을 `%LOCALAPPDATA%\report-skill\bin\` 에 복사할 때 _internal 디렉터리가 누적됩니다.

```
bin/report-skill/_internal/
├── report_skill-0.4.0.dist-info/   ← 잔존
├── report_skill-0.5.0.dist-info/   ← 새로 복사됨
├── mcp-1.27.1.dist-info/           ← 잔존
└── mcp-1.27.2.dist-info/           ← 새로 복사됨
```

`importlib.metadata`는 알파벳 순으로 먼저 찾은 걸 쓰므로 `--version` 출력이 0.4.0이라고 거짓말을 하게 됩니다. 새 파일을 복사한 뒤 반드시 구 dist-info를 `rm -rf` 해야 합니다.

---

## 7. 개발 사이클 — Audit → Implement → Ship

소스 시스템(ReportArchive)이 새 커밋을 push할 때 스킬을 갭 없이 따라가는 사이클입니다. 다중-에이전트 워크플로로 자동화하는 게 핵심입니다.

### Phase 1 — Gap Audit (다중-렌즈 + 적대적 검증)

```
8개 렌즈 (parallel finder) → 49 후보 finding
    │
    ▼
각 finding 적대적 검증 (parallel verifier, default verdict = false_positive)
    │
    ▼
34 confirmed + 7 partial + 8 false_positive 로 분류
    │
    ▼
우선순위 권장 (patch / minor / 다음)
```

렌즈 8종 예시:
1. 미반영된 최근 커밋(이번 release 윈도)
2. 백엔드 API 델타 (router/endpoint)
3. 보고서 모델 신규 필드 (collab_workspace_slugs 같은)
4. 위젯 카탈로그 델타 (content_schema 변경)
5. publish/share/cross-org 플로우
6. composites/templates/presets 모듈
7. 관련정보(엔티티/협업부서) 메타데이터
8. SKILL.md/프롬프트 자기-감사 (LLM에게 발견되는지)

각 렌즈는 finding 후보를 (gap_likely, severity)와 함께 반환. verifier는 default=false_positive로 시작해서 증거가 있을 때만 confirmed로 뒤집습니다.

### Phase 2 — Implement (per-file 병렬)

```
12개 파일 owner agent 동시 실행
    ├─ client.py       (HTTP 래퍼 추가)
    ├─ mcp_server.py   (도구 + 디스패처 + _DISPATCH)
    ├─ cli.py          (Typer 명령 + sub-app)
    ├─ report_builder.py / report_ops.py  (페이로드 필드 + update_blocks)
    ├─ adapters/*.py   (passthrough)
    ├─ bundle.py       (_TOP_KEEP)
    ├─ prompt.py       (_WIDGET_INPUT_HINTS)
    ├─ SKILL.md        (플로우 + 도구 목록)
    └─ CHANGELOG.md + pyproject.toml
```

병렬 가능한 이유: 파일이 서로 다르고, 공유 API CONTRACT가 워크플로 SHARED 컨텍스트에 박혀 있어 이름 충돌이 없음. 모든 agent가 같은 메서드명·도구명·CLI 명령어를 사용한다고 합의한 상태에서 시작.

### Phase 3 — Verify

```
pytest -x --tb=short                      → 280/280 통과
python -c "from … import _DISPATCH; print(len(_DISPATCH))"  → 66 (v0.7.x)
어댑터 sanity (rich_text/table/comparison/image)
CLI smoke (--help, 새 sub-app 노출 확인)
client method hasattr 체크 (copy_report, list_presets, …)
```

실패 시 fix 패스 한 번 → 재검증. 그래도 안 되면 멈추고 사람에게.

### Phase 4 — Build

```
python -m build --wheel             → dist/*.whl
pip install --force-reinstall <wheel>
scripts/build_release.ps1 -WithExe  → wheel zip + standalone zip
```

### Phase 5 — Release

```
git add . && git commit -F <message>           # --no-verify 절대 안 함
git tag -a vX.Y.Z -F <message>
git push origin master
git push origin vX.Y.Z
gh release create vX.Y.Z \
    dist/report_skill-X.Y.Z-py3-none-any.whl \
    dist/report-skill-vX.Y.Z.zip \
    dist/report-skill-standalone-vX.Y.Z.zip \
    --notes-file <changelog 추출>
```

### Phase 6 — Install (설치본 갱신)

```
Copy-Item dist/exe/report-skill/* %LOCALAPPDATA%\report-skill\bin\report-skill\
Copy-Item dist/exe/report-skill-mcp/* %LOCALAPPDATA%\report-skill\bin\report-skill-mcp\
Remove-Item ..\report_skill-0.X-1.0.dist-info  # 잔존 metadata 청소
report-skill --version  # vX.Y.Z 확인
```

---

## 8. 버전 정책 (SemVer 0.x)

| bump | 트리거 | 예 |
|---|---|---|
| patch (0.X.Y → 0.X.Y+1) | 버그픽스, 문서, 내부 리팩터, 어댑터 손실 보존 | 0.3.1 → 0.3.2 |
| minor (0.X.0 → 0.X+1.0) | 새 capability 표면 (새 MCP 도구, 새 위젯 필드, 새 모듈) | 0.4.0 → 0.5.0 |
| major | 호환성 깨짐 | 0.x → 1.0 |

v0.4.0이 "리졸버는 추가했지만 라이터가 없는" 비대칭을 만들었고, v0.5.0이 라이터까지 추가해 이를 해소 — 새 표면이 광범위해서 patch가 아닌 minor로 묶음.

`importlib.metadata.version("report-skill")` 가 런타임 `__version__` 의 단일 진실 — `__version__ = "..."` 하드코딩은 쓰지 않음.

---

## 9. 자주 빠지는 함정 (Lessons Learned)

### 9.1 Resolver-without-writer 비대칭

v0.4.0에 `entities_list` MCP 도구를 추가했지만 `build_create_payload` 에 `entity_ids` 슬롯이 없었습니다. → LLM이 id를 찾을 수는 있어도 보고서에 붙일 수 없음. 표면을 한 쪽만 추가하지 말 것.

체크: 새 read 도구를 추가했다면 같은 필드의 write 경로도 같은 PR에 같이 들어가야 함.

### 9.2 어댑터 패스스루 누락 = 사일런트 round-trip 손실

위젯 스냅샷에는 새 필드가 들어와 있어서 LLM이 `note`/`column_widths`/`merges`를 만들어도, adapter `normalize()` 가 화이트리스트에 없어서 드롭 → revise 라운드트립마다 사용자 수동 조정값 손실.

체크: ReportArchive `registry.py` 의 `_NOTE_FIELD` 같은 필드 추가를 발견하면, 같은 release window에서 어댑터 화이트리스트도 같이 늘려야 함.

### 9.3 Bundle `_TOP_KEEP` 누수

cross-instance dump/import 의 `_TOP_KEEP` 화이트리스트가 새 top-level 필드를 모르면 export 시 사일런트 손실. v0.5.0에서 `collab_workspace_slugs`, `entities → entity_ids` 가 누락된 상태였음.

체크: 보고서에 새 top-level 필드가 추가될 때 `bundle.ready_for_recreate` 도 동시에 업데이트.

### 9.4 PyInstaller --copy-metadata는 빌드 시점에 베이크

wheel 버전이 0.4.0인 상태에서 standalone을 빌드하면 standalone이 "0.4.0"이라고 보고. wheel을 먼저 0.5.0으로 force-reinstall한 뒤 standalone을 다시 빌드해야 함.

### 9.5 importlib.metadata는 알파벳 순서

설치본 `_internal/` 에 구·신 dist-info가 공존하면 `version()` 이 구 버전을 반환. 새 파일 복사 후 반드시 구 dist-info 제거.

### 9.6 멘션 nested in bold 사일런트 leak

마크다운 → HTML 변환에서 `**bold [mention](...)**` 가 nested 됐을 때, recursion이 fresh placeholders 스코프를 만들면 sentinel 토큰이 escape 되지 않고 HTML에 노출됨. closure 로 placeholders 맵을 공유하는 단일 `_decode()` 로 재작성해서 해결.

### 9.7 publish vs mount 용어 충돌

ReportArchive UI는 "게시"를 두 가지로 씀:
- 게시(mount) — 보드에 등록
- 게시(publish) — phase=finalized 전이 + 알림 fan-out

SKILL.md가 "MOUNT (publish to team boards)"로 단정하면 LLM이 `phase: finalized` 패치를 쓰는데, 이건 `services.update_report` 경로라서 phase_to_finalized activity + notification fan-out이 발화되지 않음. 두 도구를 분리하고(`report_publish` vs mount) SKILL.md에 명시.

### 9.8 작성자 락(author_lock) vs 세션 edit-lock

`ReportRead.author_lock_enabled` (작성자 hard-lock, owner only) 와 `/reports/{id}/lock` (세션 edit-lock, 짧은 점유) 는 별개. PATCH 시 403 "작성자가 수정 잠금" 응답이 오면 LLM이 권한 부족과 구분 못 함 → `AuthorLocked` 예외로 surface.

---

## 10. 새 capability 추가 체크리스트

ReportArchive에 새 엔드포인트 X가 추가됐다고 가정.

- [ ] `client.py` 에 1:1 HTTP 래퍼 추가 — 시그니처는 `def x(...) -> dict/list/None`
- [ ] `mcp_server.py` 에 `_tool("x", desc, input_schema)` + `_do_x(args)` + `_DISPATCH["x"] = _do_x`
- [ ] `cli.py` 에 매칭되는 Typer 명령 (필요하면 sub-app 신규)
- [ ] X가 새 필드를 반환한다면 — adapter 또는 ReportRead projection 에 노출
- [ ] X가 새 입력 필드를 받는다면 — `report_builder.py` / `report_ops.py` 페이로드 빌더에 Optional kwarg
- [ ] X가 위젯 콘텐츠 필드를 추가한다면 — 해당 adapter `normalize()` 의 화이트리스트 + `_WIDGET_INPUT_HINTS` 한 줄
- [ ] X가 보고서 top-level 필드를 추가한다면 — `bundle._TOP_KEEP` 에 추가
- [ ] `SKILL.md` 에 새 도구 항목 (Tools 섹션) + 필요시 Flow 한 단락
- [ ] `tests/` 에 어댑터 round-trip 또는 builder 페이로드 테스트
- [ ] `CHANGELOG.md` 에 entry + `pyproject.toml` 버전 bump
- [ ] `python -m pytest -x` 통과
- [ ] `len(_DISPATCH)` 카운트 증가 확인
- [ ] wheel + standalone 빌드 + `--version` 스모크

이 12개가 한 PR에 다 들어가면 새 capability가 표면 누락 없이 LLM에 닿습니다.

---

## 11. 디렉터리 레이아웃 요약

```
report-skill/
├── .claude/skills/report-write/SKILL.md   ← LLM 컨텍스트
├── src/report_skill/
│   ├── client.py
│   ├── mcp_server.py
│   ├── cli.py
│   ├── adapters/                          33개 위젯
│   ├── prompt.py
│   ├── report_builder.py
│   ├── report_ops.py
│   ├── bundle.py
│   └── data/
│       ├── widgets.snapshot.json
│       └── templates/
├── bridge/                                 LLM provider chain
├── tests/
├── scripts/
│   ├── build_release.ps1
│   ├── build_exe.ps1
│   └── refresh_bundled_data.py
├── docs/
│   ├── BUILDING.md                        (이 문서)
│   ├── RECEIVER.md                        설치 가이드 (wheel)
│   └── RECEIVER-STANDALONE.md             설치 가이드 (exe)
├── CHANGELOG.md
├── pyproject.toml
├── README.md
├── install.ps1
└── install-standalone.ps1
```

---

## 부록 — 같은 패턴으로 다른 시스템에 스킬을 만들려면

1. 대상 시스템의 HTTP API 인벤토리부터 작성. 어떤 동사(create / read / update / patch / delete)가 어떤 리소스에 있는가.
2. 위젯·블록·컴포넌트 같은 "콘텐츠 단위"가 있다면 각각 adapter를 하나씩 — `normalize(raw) -> dict` 시그니처로.
3. ID가 필요한 표면(workspace, user, entity 등)은 모두 resolver tool 로 노출 — LLM이 추측하지 못하게.
4. `SKILL.md` 는 "어떤 도구가 있고 어느 순서로 쓰는가" 만 자연어로. JSON Schema는 도구 스키마가 자동으로 제공.
5. 어댑터·도구·필드 추가는 항상 한 묶음(client + MCP + CLI + SKILL.md + CHANGELOG)으로.
6. 새 release window마다 audit 워크플로를 돌려서 갭을 좁힘 — 8 렌즈 + 적대적 verifier가 검증.

이 패턴이 잘 동작하는 이유: LLM에게 "무엇을 할 수 있는가"를 가르치는 자연어(SKILL.md), "어떻게 호출하는가"를 가르치는 스키마(MCP), "정확한 모양"을 가르치는 어댑터(normalize)가 세 갈래로 같이 가기 때문입니다. 어느 하나만 빠져도 LLM이 깨끗하게 못 씁니다.
