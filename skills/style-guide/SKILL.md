You are evaluating a technical document for **subjective style quality** —
qualities a mechanical linter cannot judge.

Evaluate ONLY these criteria:
{% for c in rubric.criteria %}
- **{{ c.name }}** (weight {{ c.weight }}): {{ c.description }}
{%- endfor %}

Score each criterion independently, 0–10, using these bands:
- 9–10: professional-quality style throughout; at most trivial lapses.
- 7–8: good, with a few noticeable style issues.
- 5–6: readable but inconsistent tone, register, or flow.
- 3–4: frequent style problems that impede reading.
- 0–2: pervasive problems; the document reads as unedited output.

Calibration example. For this passage:

> Welcome! In this amazing guide we'll explore the powerful `retry` flag.
> Sorry if this is obvious, but you should probably set it. The flag is set
> by the user. Configuration of the timeout is also performed here.

tone is 4 (marketing "amazing"/"powerful" plus a needless apology), flow is 6
(the timeout sentence arrives with no transition), voice-target is 4 (drifts
from "we'll" to "the user" and into passive "is set"/"is performed"), and it
would carry a major tone violation and a minor flow violation.

If the document is under roughly 50 words of prose, there is too little
evidence to judge style: return 5 for every criterion, confidence 0.2, an
empty violations array, and say so in the summary.

Severity definitions: {{ rubric.severity | join("; ") }}

Return a JSON object with:
- "criterion_scores": an object with one 0–10 score per criterion name above
- "score": your holistic 0–10 impression (the reported overall score is
  recomputed from "criterion_scores" and the weights, so do not average by hand)
- "confidence": 0–1 — how strongly the document supports these scores. Use
  below 0.4 when it is too short, too fragmentary, or too far off-topic to
  judge style fairly.
- "violations": array of {"severity": "minor"|"major", "rule": criterion name,
  "location": where in the document, "message": what is wrong}
- "summary": 1–2 sentences

Do not penalize factual errors, missing content, or markdown syntax — other
evaluators handle those.

Document:

<document>
{{ document }}
</document>
