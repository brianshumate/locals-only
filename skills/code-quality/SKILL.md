You are evaluating the **subjective quality of code examples** in a technical
document. Whether the code *runs* is checked mechanically elsewhere — do not
try to execute it; judge how good it is as example code.

Evaluate:
{% for c in rubric.criteria %}
- **{{ c.name }}** (weight {{ c.weight }}): {{ c.description }}
{%- endfor %}

If the document contains no code blocks, return score 5, confidence 0.2, an
empty violations array, and say so in the summary.

Scoring:
- 9–10: idiomatic, minimal, well-explained examples throughout.
- 7–8: good examples with minor lapses (dead variables, missing explanation).
- 5–6: examples work as illustration but are verbose, unidiomatic, or unexplained.
- 3–4: examples confuse more than they teach.
- 0–2: code is filler.

Return a JSON object with:
- "score": 0–10
- "confidence": 0–1
- "violations": array of {"severity": "minor"|"major", "rule": criterion name,
  "location": which code block, "message": the issue}
- "summary": 1–2 sentences

Document:

<document>
{{ document }}
</document>
