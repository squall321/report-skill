"""Verify _detect_structured_chart doesn't fire on prose."""
from report_skill import widget_suggest

cases = [
    # SHOULD detect (structured data)
    ("structured chart pairs", """매출 추이
1월, 100
2월, 145
3월, 168""", "chart"),
    ("CSV with header", """월, 매출, 이익
1월, 100, 20
2월, 145, 30""", "chart"),
    ("pure numeric scatter", """1.5, 2.3
2.0, 3.1
3.5, 4.8""", "scatter"),

    # SHOULD NOT detect (prose with commas)
    ("prose with commas", "이번 주는 결제 API, 캐시 작업, 회귀 테스트 진행. 다음 주는 PCI-DSS, 정기 점검 예정.", None),
    ("long prose cells", "백엔드 팀은 이번 주에 결제 API 통합을 완료했다, 프론트엔드는 디자인 시스템 리팩토링을 진행 중이다, QA 팀은 회귀 테스트를 마쳤다", None),
    ("sentence with multiple periods", "결과는 좋다. 하지만, 다음 주에는 PCI-DSS, 회귀 테스트가 필요하다. 추가로, 캐시 점검도 진행한다.", None),
    ("comma-joined paragraph", "팀 A, 팀 B, 팀 C 가 함께 작업했다. 결과적으로 좋았다.", None),
    ("CSV-shaped prose without numbers", """이름, 부서
김철수, 백엔드
이영희, 프론트엔드
박민수, QA""", "chart"),  # this IS valid — has consistent label,label data
]

for label, text, expected in cases:
    sug = widget_suggest.suggest_extras(text, use_llm="never")
    got = sug[0].widget_type if sug else None
    ok = "✓" if got == expected else "✗"
    print(f"  {ok} [{label}]  expected={expected}  got={got}")
