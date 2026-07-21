Estimate the length of the document below.

Count (approximately) the number of words of prose in the document. Then score:
- 10 if the document is a substantive document (over 100 words),
- 5 if it is short (20–100 words),
- 0 if it is nearly empty (under 20 words).

Return a JSON object with:
- "score": the 0–10 score above
- "confidence": your confidence in the estimate, 0–1
- "word_estimate": your approximate word count (integer)
- "violations": an empty array
- "summary": one sentence

Document:

<document>
{{ document }}
</document>
