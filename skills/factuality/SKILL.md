You are checking a technical document against a list of reference facts.

Reference facts (each is the correct statement of a checkable claim):
{% for f in reference_facts %}
{{ loop.index }}. {{ f.truth }}
{%- endfor %}

For EACH reference fact, decide how the document relates to it:
- "supported": the document states it correctly (paraphrase is fine).
- "contradicted": the document states something incompatible with it.
- "unaddressed": the document does not mention it.

Also list any additional claims in the document that you are confident are
factually wrong, independent of the reference list.

Scoring (0–10): start at 10; subtract 3 per contradicted reference fact;
subtract 2 per additional confident factual error; subtract 0.5 per
unaddressed fact that the document's topic clearly required. Floor at 0.

Return a JSON object with:
- "score": 0–10
- "confidence": 0–1
- "claims": array of {"fact_index": 1-based index into the list above,
  "status": "supported"|"contradicted"|"unaddressed",
  "evidence": quote or location in the document, empty string if unaddressed}
- "violations": array of {"severity": "major", "rule": "factual-error",
  "location": where, "message": the incorrect claim and why it is wrong}
  for contradicted facts and additional errors
- "summary": 1–2 sentences

Document:

<document>
{{ document }}
</document>
