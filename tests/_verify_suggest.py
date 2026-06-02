from report_skill import widget_suggest

cases = [
    ("User-described pattern (title + axes + data pairs)",
     """매출 추이
x: 월
y: 매출 (백만)
1월, 100
2월, 120
3월, 150
4월, 130"""),

    ("CSV with header, multi-column",
     """월, 매출, 이익
1월, 100, 20
2월, 145, 30
3월, 168, 38"""),

    ("Date list (should be milestone, NOT chart)",
     "2026-05-31 결제 통합 완료, 2026-06-15 회고, 2026-07-01 GA"),

    ("Loose timeseries (no structure)",
     "매출은 1월 100, 2월 145, 3월 168, 4월 152 였다"),

    ("Pure numeric pairs (scatter)",
     """산점도 데이터
1.5, 2.3
2.0, 3.1
3.5, 4.8
4.2, 5.5"""),

    ("Pipe-delimited",
     """월 | 매출 | 이익
1월 | 100 | 20
2월 | 120 | 25
3월 | 150 | 32"""),
]

for label, text in cases:
    sug = widget_suggest.suggest_extras(text, use_llm="never")
    print(f"\n--- {label} ---")
    if not sug:
        print("  (no suggestion)")
    for s in sug:
        print(f"  -> {s.widget_type} ({s.confidence}) — {s.matched_pattern}")
        if s.widget_type in ("chart", "scatter"):
            print(f"     props.label   : {s.props.get('label')}")
            print(f"     props.columns : {s.props.get('columns')}")
            print(f"     input (first 2): {s.input[:2]}")
        elif s.widget_type == "milestone":
            print(f"     input (first 2): {s.input[:2]}")
