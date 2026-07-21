You are evaluating a technical document for **subjective style quality** —
qualities a mechanical linter cannot judge.

Evaluate ONLY these criteria:
{% for c in rubric.criteria %}
- **{{ c.name }}** (weight {{ c.weight }}): {{ c.description }}
{%- endfor %}

Scoring:
- 9–10: professional-quality style throughout; at most trivial lapses.
- 7–8: good, with a few noticeable style issues.
- 5–6: readable but inconsistent tone, register, or flow.
- 3–4: frequent style problems that impede reading.
- 0–2: pervasive problems; the document reads as unedited output.

Severity definitions: {{ rubric.severity | join("; ") }}

Return a JSON object with:
- "score": weighted overall score, 0–10
- "confidence": 0–1
- "violations": array of {"severity": "minor"|"major", "rule": criterion name,
  "location": where in the document, "message": what is wrong}
- "summary": 1–2 sentences

Do not penalize factual errors, missing content, or markdown syntax — other
evaluators handle those.

Document:

<document>
{{ document }}
</document>
