You are evaluating whether a technical document fits its **intended
audience** — the audience is usually stated or implied in the introduction.

Evaluate:
{% for c in rubric.criteria %}
- **{{ c.name }}** (weight {{ c.weight }}): {{ c.description }}
{%- endfor %}

Scoring:
- 9–10: pitched exactly right; a reader from the audience is never lost or
  condescended to.
- 7–8: mostly right; occasional unexplained jargon or over-explanation.
- 5–6: noticeable mismatch in parts of the document.
- 3–4: substantial mismatch (expert prose for beginners, or the reverse).
- 0–2: unusable for the stated audience.

Return a JSON object with:
- "score": 0–10
- "confidence": 0–1
- "violations": array of {"severity": "minor"|"major", "rule": criterion name,
  "location": where, "message": the mismatch}
- "summary": 1–2 sentences

Document:

<document>
{{ document }}
</document>
