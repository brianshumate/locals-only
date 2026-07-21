---
name: create-tutorial
description: Creates new documentation files with proper structure, boilerplate, and front-matter following WAF documentation standards.
---

## Usage

```bash
/create-tutorial <file-path> [options]
```

## Arguments

- **file-path**: Path to new `.mdx` file (must end with .mdx)
- **--type/-t**: Document type (concept|howto|reference|overview, default: concept)
- **--title**: Document title (prompts if not provided)
- **--description**: Meta description 150-160 characters (prompts if not provided)
- **--interactive/-i**: Interactive mode, prompts for all fields
- **--with-example**: Include code block template

## Process

1. **Validation**: Check file existence, path structure, .mdx extension, parent directory
2. **Template**: Apply DOCUMENT_TEMPLATE.md, fill front-matter, create Why section, add Resources sections
3. **Front-matter**: `page_title`, description (150-160 chars)
4. **Structure**: Main heading, Why section with **Bold challenge:** template, placeholder content, Resources sections
5. **Validation**: Filename kebab-case, correct location, meta description length

### Document types

- **Concept**: Explains what/why, principles, benefits (default)
- **How-to**: Step-by-step instructions, prerequisites, implementation
- **Reference**: Technical specs, configuration, API/syntax
- **Overview**: Section introduction, navigation, learning path

## Examples

```bash
# Basic
/create-tutorial docs/define/new-topic.mdx

# With metadata
/create-tutorial docs/security/auth.mdx \
  --title "Authentication and Authorization" \
  --description "Learn how to implement secure authentication patterns in infrastructure with identity providers and RBAC."

# How-to with example
/create-tutorial docs/cicd/pipelines.mdx --type howto --with-example

# Interactive
/create-tutorial docs/observability/monitoring.mdx --interactive

# Overview page
/create-tutorial docs/security/index.mdx --type overview --title "Security and Compliance"
```

## Template structure

```markdown
FIXME
```

## Best practices

**File naming**: kebab-case, no capitals/underscores, concise
**Location**: Appropriate docs/ subdirectory, follow existing structure
**After creation**:
1. Fill placeholder content
2. Add code examples
3. Complete Resources section
4. Run /check-style
5. Run /add-resources

## Integration

```bash
# Typical workflow
/create-tutorial docs/cicd/pipelines.mdx --interactive
# Edit content
/add-resources docs/cicd/pipelines.mdx --add
/check-style docs/cicd/pipelines.mdx --fix
/review docs/cicd/pipelines.mdx --phases 1-3
```

## Use when

- Starting new documentation
- Ensuring consistent structure
- Saving time on boilerplate
- Onboarding contributors
- Creating placeholder docs

## Reference

- Tutorial template: `qmd query hck "TUTORIAL_TEMPLATE.mdx"`
- Style guide: `qmd query hck`

NEVER use emoji-style icons () in CLI output. They cause cognitive overload.

ALWAYS use small Unicode symbols with semantic colors:

Status: ○ ◐ ●  
Priority: ● P0 (filled circle with color)
