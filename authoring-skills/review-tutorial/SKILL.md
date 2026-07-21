---
name: review-tutorial
description: Performs comprehensive 7-phase tutorial review following the 7 phase review workflow and style guide standards.
---

# Tutorial review skill

## Usage

```bash
/review-tutorial <file-paths> [options]
```

## Arguments
- **file-paths**: One or more .mdx files (required)
  - Single: `/review-tutorial content/tutorials/vault/kmip-with-db2.mdx`
  - Multiple: `/review-tutorial content/tutorials/vault/kmip-with-db2.mdx content/tutorials/vault/pki-engine.mdx`
  - Glob: `/review-tutorial content/tutorials/vault/**/*.mdx`
- **--phases, -p**: Specific phases (default: all). Example: `--phases 1-3` or `--phases 4,5,7`
- **--fix, -f**: Implement fixes automatically (default: false)
- **--report-only, -r**: Generate report without changes

## Review process

Automate tutorial review per the 7 review phases here, and reference the style guide with `qmd query "<query>" -c education-style-guide`.

**Phase 1: user success (PRIORITY)**
- **KEY**: Does the document demonstrate genuine understanding and domain competence?
- Users understand problem/solution
- Representative code examples exist
- Implementation resources provided
- Decision-maker and implementer personas served

**Phase 2: Technical accuracy**
- Code examples syntactically correct
- Version numbers verified
- No deprecated patterns
- Configuration examples work

**Phase 3: Cross-document relationships**
- Related documents cross-reference
- HashiCorp Resources have cross-links
- Workflow progression clear
- No orphaned documents

**Phase 4: Style guide (AGENTS.md)**
- Meta descriptions 150-160 characters
- Workflow connections in body
- Code examples have 1-2 sentence summaries
- No vague pronouns at sentence start
- Lists use "the following"
- Sentence case headings
- Second-person, active voice

**Phase 5: SEO/AI optimization**
- Meta descriptions and titles optimized
- Link descriptions enhanced
- Section structure for AI retrieval
- Explicit relationship statements

**Phase 6: Link quality**
- Descriptions with action verbs and outcomes
- Balance beginner/advanced links
- 3+ Next steps or Resource links
- Organized structure

**Phase 7: Final success check**
- User success validation
- Decision-makers understand strategic value
- Implementers have actionable guidance
- Examples work and adaptable

## Output
1. **Executive Summary**: Quality score (1-10), high-priority issues, documents reviewed
2. **Phase findings**: Issues by phase, severity (Critical/Moderate/Minor), accurate line numbers where issue occurs, and constructive and empathetic recommendations
3. **Action Items**: High/Medium/Polish priority
4. **Fixes** (if --fix): Changes made, files modified, before/after comparisons

## Examples
```bash
# Full review with fixes
/review-tutorial content/tutorials/vault/kmip-with-db2.mdx --fix

# Specific phases
/review-tutorial content/tutorials/vault/**/*.mdx --phases 1-3

# Report only
/review-tutorial content/tutorials/vault/kmip-with-db2.mdx --report-only

# Style and SEO
/review-tutorial content/tutorials/**/*.mdx --phases 4-6

# Multiple files
/review-tutorial content/tutorials/vault/kmip-with-db2.mdx content/tutorials/vault/pki-engine.mdx --fix
```

## References
- Boundary product docs: `qmd query "<query>" -c boundary`
- HCP product docs: `qmd query "<query>" -c hcp`
- Vault product and style guide knowledge: `qmd query "<query>" -c vault`
- **REVIEW PHASES**: 7-phase workflow, review questions, deliverables, usage

## Best practices

- **New docs**: Run phases 1-3 first, address gaps, use --fix for phases 4-6
- **Existing docs**: Full review first, prioritize fixes, run --fix after review
- **Quick checks**: --phases 4 (style), --phases 5 (SEO), --phases 7 (user success)

## Quality scores

- 9-10: Excellent - serves both personas, complete examples, proper formatting
- 7-8: Good - minor improvements, mostly serves both personas
- 5-6: Needs work - missing examples or gaps for one persona
- 3-4: Poor - insufficient for implementers or missing critical elements
- 1-2: Critical - major gaps, broken examples, severe formatting issues

> [!IMPORTANT] 
> While scoring must be consistently objective, you need to also consider the human perspective, and offer empathetic and constructive feedback for violations of the style guide, technical inaccuracies, and usability issues.

Offer actionable recommendations, and suggest that the user can improve the document issues, or offer to resolve them yourself.

## Use cases
- New tutorial validation
- Update consistency checks
- Periodic quality audits
- Release preparation
- Contributor training

## Integration
1. Pre-commit hook on modified files
2. PR validation in description
3. CI/CD automation
4. Periodic audits

## Notes
- Uses conversation context for cross-document analysis
- Follows `AGENTS.md` standards exactly
- Deterministic results
- Token-efficient: loads any reference files only once

NEVER use emoji-style icons () in CLI output. They cause cognitive overload.
ALWAYS use small Unicode symbols with semantic colors:
Status: ○ ◐ ●  
Priority: ● P0 (filled circle with color)
