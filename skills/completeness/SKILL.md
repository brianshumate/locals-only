You are evaluating whether a technical document is **complete** for its
stated purpose.

Evaluate:
{% for c in rubric.criteria %}
- **{{ c.name }}** (weight {{ c.weight }}): {{ c.description }}
{%- endfor %}

Scoring:
- 9–10: everything a reader needs is present; no gaps.
- 7–8: complete in structure, minor gaps in detail.
- 5–6: a required section is thin or prerequisites are partly missing.
- 3–4: a required section is missing or steps cannot be followed to success.
- 0–2: fragmentary.

Return a JSON object with:
- "score": 0–10
- "confidence": 0–1
- "violations": array of {"severity": "minor"|"major", "rule": criterion name,
  "location": section or area, "message": what is missing or insufficient}
- "summary": 1–2 sentences

Do not penalize style, factual errors, or code correctness — other evaluators
handle those.

Document:

<document>
{{ document }}
</document>
