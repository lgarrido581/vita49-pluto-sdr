# Claude Code Plans

This directory stores implementation plans created during plan mode sessions.

## Directory Purpose

Plans are automatically saved to the global directory (`C:\Users\Luis\.claude\plans\`) during plan mode, but should also be copied here for:
- **Version control**: Track planning decisions with code
- **Team visibility**: Share design decisions with collaborators
- **Project documentation**: Keep plans alongside implementation

## Workflow

When Claude exits plan mode:
1. Full plan is saved to global directory (automatic)
2. Plan summary is saved to `docs/` (manual)
3. Full plan is copied here to `.claude/plans/` (manual)

## Naming Convention

Plans should use descriptive date-prefixed names:
- Format: `YYYY-MM-DD-descriptive-name.md`
- Example: `2026-02-07-multichannel-burst-mode.md`

## Current Plans

- `2026-02-07-multichannel-burst-mode.md` - Multi-channel burst mode implementation

## Git Integration

Add to `.gitignore` if you want to keep plans local:
```
.claude/plans/*.md
```

Or commit them if you want plans version-controlled with code.
