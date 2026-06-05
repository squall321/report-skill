# Making a Claude Skill — 자동화 프로그램을 LLM이 직접 조작하게 만드는 레시피

이 문서는 ReportArchive 같은 특정 시스템과 무관하게, "내가 만든 (혹은 회사에서 쓰는) 자동화 프로그램 / 웹앱 / 백오피스 도구 X 를 클로드가 직접 운전하게 하려면 어떻게 만드는가" 를 시스템 무관하게 정리한 레시피입니다.

`report-skill`은 이 레시피의 한 인스턴스이고, 이 문서는 그 추상화입니다.

---

## 0. 사전 조건 — 스킬화가 가능한지 먼저 확인

| 조건 | 설명 |
|---|---|
| 외부 호출 인터페이스 | HTTP REST, gRPC, Python 라이브러리, OS 명령행 — 어떤 형태든 LLM 프로세스가 호출할 수 있어야 함. UI 자동화(Selenium 등)도 가능하지만 어댑터 비용이 큼. |
| 멱등성·관찰성 | 호출 결과를 LLM이 검증할 수 있어야 함 (응답 본문 / 에러 코드 / 후속 GET). 사일런트 행위는 디버그가 불가능. |
| 안정 식별자 | 쓰기 대상이 id / slug / path 등 안정적 식별자를 가져야 함. LLM이 추측하지 않도록 resolver 표면이 만들 수 있는가? |
| 인증 형태 | 토큰/세션/쿠키/OAuth — 어떤 형태든 환경변수나 keyring 으로 LLM 프로세스에 주입 가능해야 함. |

위 4개 중 하나라도 안 되면 스킬화는 가능하지만 비용이 큽니다. 일단 안 된다고 가정하고 우회로(예: HTTP API를 새로 노출)를 먼저 만들 가치가 있습니다.

---

## 1. 클로드 스킬의 정의 — 4 표면

스킬은 LLM이 외부 시스템을 안전하게 운전할 수 있게 4개 표면을 한 묶음으로 제공하는 코드 패키지입니다.

```
┌─────────────────────────────────────────────────────────┐
│ SKILL.md         — LLM이 system context로 읽는 자연어 명세  │
└─────────────────────────────────────────────────────────┘
                          │ (LLM이 어떤 도구를 호출할지 결정)
                          ▼
┌─────────────────────────────────────────────────────────┐
│ MCP 서버         — 도구 등록 + JSON Schema + 디스패처       │
│ CLI             — 같은 도구를 사람/CI가 명령행에서 호출        │
└─────────────────────────────────────────────────────────┘
                          │ (둘 다 같은 Client를 호출)
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Client          — 외부 시스템 API 의 얇은 1:1 래퍼          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
                    대상 시스템 (HTTP / gRPC / lib …)
```

| 표면 | 역할 |
|---|---|
| `SKILL.md` | "무엇을 할 수 있고, 언제, 어떤 순서로 도구를 부르는가" — 워크플로 / 룰 / 예시 |
| MCP 서버 | LLM이 `tool_use`로 호출. 입력은 JSON Schema 로 검증, 출력은 JSON. |
| CLI | 사람이 디버그·테스트·배치 작업할 때. MCP와 같은 동작. |
| Client | httpx / requests / gRPC stub … 외부 호출 1:1 래퍼. 비즈니스 로직 없음. |

---

## 2. Skill Stack — 표준 아키텍처

```
.claude/skills/<skill-name>/SKILL.md   ← LLM 컨텍스트
        ▲
        │
src/<skill_pkg>/
├── mcp_server.py     MCP stdio 서버 + _DISPATCH dict
├── cli.py            Typer (또는 Click) sub-apps
├── client.py         API 1:1 래퍼 (httpx/grpc/lib)
│
├── adapters/         콘텐츠 단위가 있을 때 (위젯/블록/메시지 등)
│   ├── <unit_a>.py   normalize(raw) -> dict
│   └── <unit_b>.py
│
├── prompt.py         LLM 프롬프트 빌더 + _INPUT_HINTS
├── builder.py        복합 페이로드 조립
├── ops.py            낙관적 동시성·재시도·세션 관리
├── bundle.py         cross-instance dump/import (있다면)
│
└── data/             외부 시스템의 카탈로그/스키마 스냅샷
    └── <catalog>.json
```

8 계층의 의미:

1. **SKILL.md** — LLM에게 보여줄 자연어 (Tools / Flows / Rules)
2. **mcp_server.py** — `_DISPATCH: dict[str, Callable]` 가 도구명 → 디스패처 매핑
3. **cli.py** — `app.add_typer(...)` 로 sub-app 트리
4. **client.py** — 외부 시스템 메서드 1:1
5. **adapters/** — 자유 입력 → 정형 출력 정규화 (콘텐츠 단위가 있을 때)
6. **prompt.py** — 위젯별 input hint + 프롬프트 빌더
7. **builder.py / ops.py** — 복합 액션 (생성/패치 페이로드, 재시도)
8. **data/** — 외부 카탈로그 스냅샷 (외부 lambda/동적 스키마를 빌드 타임에 dump)

콘텐츠 단위(블록/위젯/메시지)가 없는 단순 API 래퍼라면 `adapters/`, `builder.py`, `data/` 는 생략 가능. 최소 구성은 `mcp_server.py + cli.py + client.py + SKILL.md` 4 파일.

---

## 3. 황금률 — 3-view 패턴

새 capability 1개 = 정확히 3개 표면 동시 추가. 시그니처와 이름이 항상 일치해야 합니다.

```
새 외부 동작 X
    │
    ├─ client.py:        def x(self, ...) -> dict
    ├─ mcp_server.py:    _tool("x", desc, schema) + _do_x(args) + _DISPATCH["x"]
    └─ cli.py:           @<group>_app.command("x") def x(...)
```

세 표면의 파라미터·타입·기본값은 같아야 합니다. 다르면 SKILL.md가 어느 한 쪽에 맞춰져서 LLM이 다른 쪽을 호출할 때 깨집니다.

### MCP 도구 등록 템플릿

```python
# mcp_server.py
_tool(
    "x",
    "한 줄 설명 — LLM이 이걸 보고 도구를 선택함",
    {
        "type": "object",
        "required": ["required_arg"],
        "properties": {
            "required_arg": {"type": "integer"},
            "optional_arg": {"type": "string"},
        },
    },
)

def _do_x(args: dict) -> Any:
    with Client() as c:
        return c.x(int(args["required_arg"]), optional=args.get("optional_arg"))

_DISPATCH["x"] = _do_x
```

### CLI 미러 템플릿

```python
# cli.py
@x_app.command("run")
def x_run(
    required_arg: int,
    optional_arg: str | None = typer.Option(None, "--optional"),
):
    """한 줄 설명 — --help 에 출력."""
    with Client() as c:
        rich.print_json(data=c.x(required_arg, optional=optional_arg))
```

### Client 메서드 템플릿

```python
# client.py
class Client:
    def x(self, required_arg: int, *, optional: str | None = None) -> dict:
        body = {"required_arg": required_arg}
        if optional is not None:
            body["optional"] = optional
        r = self._http.post(self._url("/x"), json=body)
        r.raise_for_status()
        return r.json()
```

### 이름 매핑 컨벤션

| 위치 | 형태 | 예 |
|---|---|---|
| Client 메서드 | `verb_resource` | `create_report`, `list_folders` |
| MCP 도구 | `resource_verb` 또는 `resource_plural_action` | `report_create`, `folders_list` |
| CLI 명령 | `<resource-group> <verb>` | `report create`, `folders list` |

세 이름이 1:1 매핑되면 LLM 입장에서 "도구를 호출했더니 CLI로 같은 걸 재현할 수 있다"가 보장됩니다.

---

## 4. 어댑터 패턴 — 콘텐츠 정규화

대상 시스템이 "블록 / 위젯 / 메시지 / 셀" 같은 콘텐츠 단위를 받는다면, 각 단위마다 어댑터를 만듭니다.

```python
# adapters/<unit>.py
class XAdapter(Adapter):
    type = "<unit_type>"

    def normalize(self, raw: Any) -> dict:
        if isinstance(raw, list):
            return self._from_list(raw)               # 레거시 입력 관용
        if not isinstance(raw, dict):
            return self._default()
        out = self._required_fields(raw)
        self._apply_passthrough(raw, out)             # 화이트리스트만 통과
        return out
```

### 어댑터 4 원칙

1. **화이트리스트 패스스루** — 외부 스키마에 정의된 필드만 통과. 모르는 필드는 조용히 버린다 (외부 시스템이 `additionalProperties=False` 면 400). 새 필드가 외부에 추가되면 어댑터에도 동시 추가.
2. **레거시 입력 관용** — LLM이 dict 대신 list를 줘도 동작하도록 `isinstance` 분기. 새 필드는 항상 Optional.
3. **무손실 round-trip** — `fetch → normalize → patch` 가 같은 필드 집합 유지. 깨지면 사용자 수동 조정값이 매번 사일런트 손실 (이게 가장 자주 빠지는 함정).
4. **placeholder + sentinel** — 마크다운 → HTML 같은 변환에서 nested 구조는 sentinel 문자(`\x00token1\x00`)로 자리잡고 마지막에 한 번에 decode. recursion 시 closure 로 placeholders 맵을 공유해야 nested 케이스가 깨지지 않음.

---

## 5. 스키마 주도 프롬프트

LLM이 입력 모양을 정확히 만들도록 두 가지를 함께 주입합니다.

### (a) 카탈로그 스냅샷

외부 시스템이 위젯/블록/리소스 스키마를 API로 노출한다면 → 빌드 타임 / 런타임에 fetch 해서 wheel에 번들. 스키마가 동적 람다라면 read-only subprocess 로 dump.

```python
# scripts/refresh_bundled_data.py
def main():
    catalog = fetch_catalog_from_live_backend()
    Path("src/<skill_pkg>/data/catalog.snapshot.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
```

번들 이유: 사용자가 외부 시스템에 접속하지 않아도 도구 스키마가 정확하게 LLM에 보임 (catalog sync 없이도 첫 호출이 통과).

### (b) 입력 힌트 — `_INPUT_HINTS`

JSON Schema 만으로 부족한 인간-언어 가이드를 단위 타입별로 1-3줄 추가.

```python
# prompt.py
_INPUT_HINTS = {
    "<unit_type_a>": (
        "Required: foo. Optional: bar (rendered with prefix automatically — do NOT include it yourself). "
        "Resolve <id_kind> via <resolver_tool>."
    ),
    "<unit_type_b>": "Required: baz. …",
}

def build_create_prompt(unit_type: str, ...) -> str:
    schema_block = json.dumps(_CATALOG[unit_type]["schema"], indent=2)
    hint = _INPUT_HINTS.get(unit_type, "")
    return f"... {schema_block} ... {hint} ..."
```

프롬프트 빌더는 단위 타입에 맞는 힌트를 JSON Schema 섹션 뒤에 자동으로 붙입니다. 룰("NEVER invent ids", "preserve unchanged fields")은 모든 프롬프트의 preamble에 박힙니다.

### (c) 리졸버 도구

`id` / `slug` 가 필요한 자리는 LLM이 절대 추측하지 않도록 read-only resolver 도구로 강제.

```python
# 패턴: <resource>_list / <resource>_search
"workspaces_list"     -> 워크스페이스 후보 목록
"users_search"        -> 자유 텍스트로 사용자 검색
"<axis>_types_list"   -> 분류 축 (model_name, customer 같은)
"<entity>_list"       -> 분류 축별 엔티티
```

룰: SKILL.md 와 프롬프트 빌더 preamble 모두에 "NEVER invent ids — resolve via tools" 명시.

---

## 6. 패키징 + 배포

### 두 가지 배포 방식

| 방식 | 대상 | 산출물 | 크기 |
|---|---|---|---|
| **Wheel** | Python 있는 사용자 | `<skill_pkg>-X.Y.Z-py3-none-any.whl` + `install.ps1` | ~200 KB |
| **Standalone** | Python 없는 사내 PC | PyInstaller `--onedir --noupx --copy-metadata` | ~30-40 MB |

### PyInstaller 옵션 (Windows)

```powershell
pyinstaller --onedir --noupx --copy-metadata <skill-name> `
    --name <skill-name> `
    --hidden-import <skill_pkg>.mcp_server `
    src/<skill_pkg>/cli.py
```

- `--copy-metadata <pkg>` 로 wheel METADATA 를 exe 에 베이크 → `importlib.metadata.version(pkg)` 가 런타임에 이걸 읽음
- `--noupx` 로 Defender 오탐 회피
- `--onedir` 로 `--onefile` 대비 시작 빠르고 dist-info 청소 쉬움

### 배포 산출물 구조

```
release-zip/<skill-name>-vX.Y.Z/
├── <skill_pkg>-X.Y.Z-py3-none-any.whl
├── .claude/skills/<skill-name>/SKILL.md
├── .env.example
├── install.ps1
└── README.md
```

수신자: 압축 풀고 `.\install.ps1` 한 번 → 끝. `.claude/skills/` 가 Claude Code 의 스킬 폴더라 자동 로드.

---

## 7. 개발 사이클 — Audit → Implement → Ship

외부 시스템이 새 커밋을 push할 때 스킬을 갭 없이 따라가는 6 phase 사이클.

### Phase 1 — Gap Audit (다중 렌즈 + 적대적 검증)

```
N개 렌즈 (parallel finder)
    │  (예: 백엔드 API 델타 / 모델 신규 필드 / 카탈로그 델타 / 
    │   새 모듈 / 메타데이터 변화 / SKILL.md 자기-감사)
    ▼
각 finding 적대적 검증 (parallel verifier — default verdict = false_positive)
    ▼
confirmed_gap + partial_coverage + false_positive 분류
    ▼
우선순위 권장 (patch / minor / 다음)
```

각 렌즈는 finding을 (gap_likely, severity)와 함께 반환. verifier 는 default=false_positive 로 시작해서 증거가 강할 때만 confirmed 로 뒤집습니다 (오탐 방지).

### Phase 2 — Implement (per-file 병렬)

```
파일 owner agent 동시 실행
    ├─ client.py       (HTTP 래퍼 추가)
    ├─ mcp_server.py   (도구 + 디스패처)
    ├─ cli.py          (명령 + sub-app)
    ├─ adapters/*.py   (passthrough)
    ├─ prompt.py       (_INPUT_HINTS)
    ├─ SKILL.md        (Flows + Tools)
    └─ CHANGELOG + pyproject.toml
```

병렬 가능 조건: 모든 agent 가 같은 **API CONTRACT**(메서드명·도구명·CLI명·파라미터)를 공유. CONTRACT 가 워크플로 shared 컨텍스트에 박혀 있어 이름 충돌이 없음.

### Phase 3 — Verify

```
pytest -x --tb=short                           → 모든 테스트 통과
len(_DISPATCH)                                 → 도구 카운트 증가 확인
어댑터 sanity (각 단위 타입 한 케이스씩)
CLI smoke (--help, 새 sub-app 노출)
client method hasattr 체크
```

실패 시 fix 패스 한 번 → 재검증. 그래도 안 되면 멈춤.

### Phase 4 — Build

```
python -m build --wheel
pip install --force-reinstall dist/*.whl       # standalone 빌드 전 필수
pyinstaller ...                                 # standalone
```

### Phase 5 — Release

```
git commit -F <msg>                            # --no-verify 절대 안 함
git tag -a vX.Y.Z -F <msg>
git push origin main
git push origin vX.Y.Z
gh release create vX.Y.Z dist/*.whl dist/*.zip --notes-file <changelog>
```

### Phase 6 — Install (수신자 갱신)

```
Copy-Item dist/exe/* %LOCALAPPDATA%\<skill-name>\bin\
Remove-Item ...\<pkg>-<old>.dist-info          # 잔존 metadata 청소 필수
<skill-name> --version                          # vX.Y.Z 확인
```

---

## 8. 버전 정책 (SemVer 0.x)

| bump | 트리거 | 예 |
|---|---|---|
| patch (0.X.Y → 0.X.Y+1) | 버그픽스 / 문서 / 내부 리팩터 / 어댑터 손실 보존 | 0.3.1 → 0.3.2 |
| minor (0.X.0 → 0.X+1.0) | 새 capability 표면 (새 MCP 도구 / 새 위젯 필드 / 새 모듈) | 0.4.0 → 0.5.0 |
| major | 호환성 깨짐 | 0.x → 1.0 |

`importlib.metadata.version("<skill-name>")` 가 런타임 `__version__` 단일 진실. 하드코딩 `__version__ = "..."` 금지.

---

## 9. 자주 빠지는 함정 (시스템 무관)

### 9.1 Resolver-without-writer 비대칭

리졸버 도구만 추가하고 writer 경로를 빠뜨리면 LLM이 id를 찾을 수는 있어도 어디에도 못 붙입니다.

체크: 새 read 도구 추가 시 같은 필드의 write 경로(POST/PATCH 페이로드 슬롯) 가 같은 PR 에 함께 들어가야 함.

### 9.2 어댑터 패스스루 누락 = 사일런트 round-trip 손실

외부 스키마에 새 필드가 들어왔는데 어댑터 화이트리스트가 그대로면, fetch → normalize → patch 에서 사용자 수동 조정값이 사일런트 손실.

체크: 외부 시스템의 스키마 정의 파일 변경을 발견하면 같은 release window 에서 어댑터 화이트리스트도 동시 확장.

### 9.3 Bundle / dump-import 화이트리스트 누수

cross-instance dump/import 가 있을 때 `_TOP_KEEP` 또는 비슷한 화이트리스트가 새 top-level 필드를 모르면 export 시 사일런트 손실. 새 필드 추가 = 화이트리스트도 동시에.

### 9.4 PyInstaller `--copy-metadata` 베이크 시점

wheel 버전이 X 인 상태에서 standalone 을 빌드하면 standalone 이 X 라고 보고. wheel 을 먼저 X+1 로 `--force-reinstall` 한 뒤 standalone 을 다시 빌드해야 함.

### 9.5 `importlib.metadata` 알파벳 순서

설치본 `_internal/` 에 구·신 dist-info 가 공존하면 `version()` 이 알파벳 순서로 먼저 찾은 걸 반환 → 거짓 버전. 새 파일 복사 후 반드시 구 dist-info `rm -rf`.

### 9.6 nested 변환에서 sentinel leak

마크다운 → HTML 같은 변환에서 nested 구조(`**bold [link](...)**`)가 recursion 시 fresh placeholders 스코프를 만들면 sentinel 토큰이 escape 되지 않고 출력에 노출. closure 로 placeholders 공유하는 단일 `_decode()` 로 작성.

### 9.7 동음이의어 (alias) 충돌

대상 시스템 UI 가 같은 단어를 두 의미로 쓰는 경우(예: "게시"가 "보드 등록"이자 "발행"). SKILL.md 가 한 쪽으로 단정하면 LLM 이 다른 동작을 트리거하는 잘못된 도구를 부름. 두 도구를 분리하고 SKILL.md 에 명시적 disambiguation 단락.

### 9.8 권한 / lock 에러를 단순 403 으로 surface

외부 시스템이 권한 부족 / 작성자 lock / 동시편집 충돌 등 서로 다른 의미를 같은 HTTP 코드로 반환할 수 있음. Client 의 ApiError 핸들링에서 응답 본문 패턴을 보고 구조화된 예외(`AuthorLocked`, `RevisionMismatch` 등)로 변환해야 LLM 이 적절히 재시도 가능.

---

## 10. 새 capability 추가 체크리스트 (12 항목)

외부 시스템에 새 동작 X 가 추가됐다고 가정. 한 PR 에 다음이 다 들어가야 LLM 표면 누락 없이 닿습니다.

- [ ] `client.py` 1:1 HTTP 래퍼 추가
- [ ] `mcp_server.py` `_tool() + _do_*() + _DISPATCH`
- [ ] `cli.py` 매칭 Typer 명령 (필요시 sub-app 신규)
- [ ] X 가 새 응답 필드를 가지면 → 어댑터 또는 read projection 노출
- [ ] X 가 새 입력 필드를 받으면 → `builder.py` 페이로드 Optional kwarg
- [ ] X 가 콘텐츠 단위 필드를 추가하면 → adapter `normalize()` 화이트리스트 + `_INPUT_HINTS`
- [ ] X 가 top-level 필드를 추가하면 → `bundle._TOP_KEEP`
- [ ] `SKILL.md` 새 도구 항목 + 필요시 Flow 한 단락
- [ ] `tests/` round-trip 또는 빌더 페이로드 테스트
- [ ] `CHANGELOG.md` entry + `pyproject.toml` 버전 bump
- [ ] `python -m pytest -x` 통과
- [ ] `len(_DISPATCH)` 카운트 + `--version` 스모크

---

## 11. 초기화 레시피 (0 → 1)

새 자동화 대상 X 에 스킬을 처음 만들 때.

### Step 1 — API 인벤토리

외부 시스템의 모든 외부 호출 가능한 표면을 표로 정리.

| Verb | Resource | Path / 호출 | 입력 | 출력 |
|---|---|---|---|---|
| GET | resources | /resources | (query) | list |
| POST | resource | /resources | body | resource |
| PATCH | resource | /resources/{id} | body | resource |
| DELETE | resource | /resources/{id} | — | — |

이 표가 1차 client.py 메서드 목록입니다.

### Step 2 — 콘텐츠 단위 식별

쓰기 표면 중 "복합 콘텐츠" 가 있는가? (블록/메시지/위젯/문서) 있다면 → 각각 어댑터. 없으면 → 어댑터 계층 생략.

### Step 3 — ID 표면 식별

write 페이로드의 모든 id 필드를 나열. 각 id 가 어떤 list/search 엔드포인트로 발견 가능한가? → resolver 도구 목록.

### Step 4 — 프로젝트 초기화

```
<skill-name>/
├── pyproject.toml
├── src/<skill_pkg>/
│   ├── __init__.py
│   ├── client.py
│   ├── mcp_server.py
│   ├── cli.py
│   └── settings.py        # pydantic-settings 로 env 읽기
├── .claude/skills/<skill-name>/SKILL.md
├── tests/
├── .env.example
└── README.md
```

`pyproject.toml` 최소 설정:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "<skill-name>"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.4",
    "typer>=0.12",
    "rich>=13.7",
    "jsonschema>=4.22",
    "mcp>=1.0",
]

[project.scripts]
<skill-name> = "<skill_pkg>.cli:app"
<skill-name>-mcp = "<skill_pkg>.mcp_server:main"

[tool.setuptools.packages.find]
where = ["src"]
```

### Step 5 — Client 첫 메서드 (ping)

가장 단순한 GET 한 개로 연결 확인부터:

```python
# client.py
import httpx
from .settings import Settings

class Client:
    def __init__(self):
        self._cfg = Settings()
        self._http = httpx.Client(timeout=30.0, auth=...)
    def __enter__(self): return self
    def __exit__(self, *a): self._http.close()
    def _url(self, path: str) -> str: return f"{self._cfg.base_url}{path}"

    def ping(self) -> dict:
        r = self._http.get(self._url("/healthz"))
        r.raise_for_status()
        return r.json()
```

### Step 6 — MCP + CLI 미러

```python
# cli.py
import typer, rich
from .client import Client
app = typer.Typer(help="<skill-name> — automation skill for X")

@app.command()
def ping():
    """Healthcheck."""
    with Client() as c: rich.print_json(data=c.ping())

if __name__ == "__main__": app()
```

```python
# mcp_server.py
import asyncio, json
from mcp.server import Server
from mcp.types import Tool, TextContent
from .client import Client

server = Server("<skill-name>")
_TOOLS: list[Tool] = []
_DISPATCH: dict[str, callable] = {}

def _tool(name: str, desc: str, schema: dict):
    _TOOLS.append(Tool(name=name, description=desc, inputSchema=schema))

_tool("ping", "Healthcheck.", {"type": "object", "properties": {}})
def _do_ping(_args: dict):
    with Client() as c: return c.ping()
_DISPATCH["ping"] = _do_ping

@server.list_tools()
async def list_tools(): return _TOOLS

@server.call_tool()
async def call_tool(name: str, args: dict):
    result = _DISPATCH[name](args)
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

def main():
    from mcp.server.stdio import stdio_server
    asyncio.run(_serve())

async def _serve():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())

if __name__ == "__main__": main()
```

### Step 7 — SKILL.md 첫 플로우

```markdown
# <skill-name>

Skill for automating <X>.

## Tools

- `ping` — Healthcheck. Use to confirm connectivity before any write.

## Flow A — Smoke check

1. Call `ping` first. If it errors, stop and report.

## Rules

- NEVER invent ids. Use resolver tools (added in later releases).
```

### Step 8 — 첫 capability 추가

API 인벤토리에서 하나 골라서 3-view 패턴으로 추가 (Section 3 템플릿 그대로). SKILL.md 의 Tools 섹션에 한 줄 추가, Flow B 한 단락 추가.

### Step 9 — 어댑터 첫 개 (해당 시)

콘텐츠 단위 X 가 있으면 `adapters/x.py` 한 개 만들어서 normalize → write 까지 round-trip 테스트.

### Step 10 — 카탈로그 / 스키마 번들

외부 시스템 스키마가 동적이라면 `scripts/refresh_bundled_data.py` 추가 → `data/catalog.snapshot.json` 으로 dump.

### Step 11 — 패키징

```powershell
python -m build --wheel
pip install --force-reinstall dist/*.whl
<skill-name> ping        # 동작 확인
```

원하면 PyInstaller standalone 도 추가 — `scripts/build_release.ps1 -WithExe` 같은 래퍼 스크립트로.

### Step 12 — Claude Code 통합

`.claude/skills/<skill-name>/SKILL.md` 가 Claude Code 의 스킬 폴더에 있으면 자동 로드. MCP 서버는 `~/.claude/mcp.json` 또는 Claude Desktop 의 mcp 설정에 stdio 항목으로 등록:

```json
{
  "mcpServers": {
    "<skill-name>": {
      "command": "<skill-name>-mcp"
    }
  }
}
```

여기까지가 0 → 1. 이후는 Section 7의 Audit → Implement → Ship 사이클로 capability 를 늘려갑니다.

---

## 12. 디렉터리 / 파일 인덱스 (skeleton)

```
<skill-name>/
├── pyproject.toml                      이름 + 버전 + 의존성 + entry points
├── README.md                           사용자용 1-page 가이드
├── CHANGELOG.md                        version history
├── install.ps1                         wheel 설치 (.claude/skills 복사 포함)
├── install-standalone.ps1              standalone exe 설치 (Python 없는 환경)
├── .env.example                        토큰 / URL 템플릿
│
├── .claude/skills/<skill-name>/
│   └── SKILL.md                        LLM이 읽는 명세 (Tools / Flows / Rules)
│
├── src/<skill_pkg>/
│   ├── __init__.py
│   ├── client.py                       API 1:1 래퍼
│   ├── mcp_server.py                   MCP stdio + _DISPATCH
│   ├── cli.py                          Typer sub-apps
│   ├── settings.py                     pydantic-settings (env)
│   ├── adapters/                       콘텐츠 단위가 있을 때
│   │   └── __init__.py
│   ├── prompt.py                       LLM 프롬프트 빌더 (있을 때)
│   ├── builder.py                      복합 페이로드 (있을 때)
│   ├── ops.py                          재시도 / 락 / revision (있을 때)
│   ├── bundle.py                       cross-instance dump/import (있을 때)
│   └── data/
│       └── catalog.snapshot.json       외부 스키마 스냅샷
│
├── tests/
│   ├── test_adapters.py
│   ├── test_builder.py
│   └── test_client_smoke.py
│
├── scripts/
│   ├── build_release.ps1               wheel + standalone 빌드
│   ├── build_exe.ps1                   PyInstaller
│   └── refresh_bundled_data.py         catalog snapshot 갱신
│
└── docs/
    ├── BUILDING.md                     스킬 내부 메소드 (이 레시피의 인스턴스)
    ├── RECEIVER.md                     wheel 설치 가이드
    └── RECEIVER-STANDALONE.md          exe 설치 가이드
```

최소 시작은 위 트리에서 굵게 표시한 것만 (`pyproject.toml` + `src/<skill_pkg>/{client,cli,mcp_server,settings}.py` + `.claude/skills/<skill-name>/SKILL.md` + `.env.example`).

---

## 13. 시스템별 변형 노트

### HTTP REST 대상
- `httpx.Client` 동기 모드 + `raise_for_status()` 패턴이 가장 단순
- 인증은 `httpx.BasicAuth` / 토큰 헤더 / 쿠키 jar
- 멱등성 키, expected_revision/ETag 같은 낙관적 동시성 헤더가 있으면 `ops.py` 에서 자동 재시도

### gRPC 대상
- `client.py` 가 grpc stub 을 감싸는 형태
- 입력은 protobuf 메시지 → MCP 도구 스키마는 protobuf 의 JSON Schema 매핑
- 스트리밍 응답은 LLM 입장에서 다루기 어려움 → unary 로 감싸기

### CLI 자동화 대상 (외부 명령행 도구)
- `client.py` 가 `subprocess.run` 래퍼
- 보안: shell=False, 인자는 list 로, 사용자 입력 sanitize
- 출력은 stdout/stderr → JSON 파싱 또는 line-delimited

### UI 자동화 대상 (Selenium 등)
- 가능하지만 비용 큼. 가급적 같은 시스템이 노출하는 HTTP API 로 우회
- 어쩔 수 없다면 page object → adapter 로 추상화

### 로컬 라이브러리 대상 (예: pandas, sklearn)
- MCP 서버가 같은 venv 안에서 직접 호출 → client 계층 없이 어댑터만으로 충분
- 안전성: 임의 코드 실행 표면이 되지 않도록 도구 스키마를 좁게

---

## 부록 — 이 레시피가 잘 동작하는 이유

LLM 에게 외부 시스템 운전을 시킬 때 세 갈래가 함께 가야 합니다:

1. **자연어 명세** (SKILL.md) — "무엇을 할 수 있고 언제 쓰는가"
2. **호출 스키마** (MCP 도구) — "어떻게 호출하는가"
3. **콘텐츠 정규화** (어댑터) — "정확한 모양"

어느 하나만 빠져도 LLM 이 깨끗하게 못 씁니다. 자연어만 있고 스키마가 없으면 도구 호출 자체가 안 되고, 스키마만 있고 어댑터가 없으면 페이로드 모양이 어긋나고, 어댑터만 있고 자연어가 없으면 LLM 이 언제 도구를 부를지 모릅니다.

3-view 패턴(client + MCP + CLI)이 황금률인 이유도 같습니다 — CLI 가 있으면 사람이 LLM 의 행동을 재현·검증할 수 있고, MCP 가 있으면 LLM 이 호출할 수 있고, Client 가 둘을 공유하면 한 곳의 버그가 두 곳을 동시에 망치지 않습니다.

마지막으로 audit → implement → ship 사이클은 "외부 시스템이 빨리 바뀐다" 는 전제하에서 스킬이 사일런트 갭 없이 따라가게 하는 장치입니다. 다중 렌즈 + 적대적 검증이 사람 한 명이 놓치기 쉬운 표면을 자동으로 찾아냅니다.

이 셋이 같이 가면 어떤 자동화 프로그램이든 LLM-쓰기 가능한 표면으로 전환할 수 있습니다.
