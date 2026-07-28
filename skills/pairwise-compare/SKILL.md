Two documents below were written from the same specification. Decide which
one is the better piece of technical documentation **overall**: accurate,
complete, well-styled, and fit for its audience.

Rules:
- Judge only the documents as given; do not reward length for its own sake.
- Choose "tie" only when you genuinely cannot rank them.
- Position in this prompt carries no information about quality.

Return a JSON object with:
- "winner": "a" | "b" | "tie"
- "confidence": 0–1 — how clear the margin between the two documents is. Use
  below 0.4 when they are close enough that the ranking could plausibly flip.
- "reason": 1–2 sentences naming the decisive difference

Document A:

<document_a>
{{ document_a }}
</document_a>

Document B:

<document_b>
{{ document_b }}
</document_b>
