# Agent Repair Playbooks

This directory is the repo-local execution standard for PaperFit agents.

Use these playbooks when an agent needs category-specific guidance but cannot rely on host-installed Codex skills. They complement, not replace, the existing skill files under `PaperFit-release/skills/`.

## Mapping

| Category | Playbook | Primary Skill |
| --- | --- | --- |
| A | `category-a-space-utilization.md` | `skills/space-util-fixer/SKILL.md` |
| B | `category-b-floats.md` | `skills/float-optimizer/SKILL.md` |
| C | `category-c-consistency.md` | `skills/consistency-polisher/SKILL.md` |
| D | `category-d-overflow.md` | `skills/overflow-repair/SKILL.md` |
| E | `category-e-template-migration.md` | `skills/template-migrator/SKILL.md` |

## Global Invariants

1. Recompile after each meaningful repair batch.
2. Check rendered page images before claiming success.
3. Preserve academic meaning and bibliography integrity.
4. Never use `\resizebox` or `\scalebox` as the default table-width fix.
5. Never let float moves cross the bibliography boundary.
6. Prefer transactional write + rollback-capable workflows.

## Recommended Command Flow

1. `paperfit fix-layout --main <main.tex> --template <template> --target-pages <n>`
2. `paperfit check-visual --main <main.tex> --template <template> --target-pages <n>`
3. For bounded debugging:
   `paperfit run scripts/repair_plan_generator.py ...`
   `paperfit run scripts/repair_plan_executor.py ...`

## Acceptance Standard

A repair is acceptable only when all four conditions hold:

1. compile succeeds;
2. visual issue is reduced or eliminated in page images;
3. content-integrity checks pass;
4. no new high-severity regression is introduced in another family.
