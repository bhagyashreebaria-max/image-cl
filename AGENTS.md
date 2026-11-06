# image cl development guidance

Read `ARCHITECTURE.md` before changing runtime, workflows, upload handling or
deployment. It records the owner's composable Go gateway + Python worker design
and distinguishes implemented behavior from outstanding requirements.

- Work on a separate feature branch. Do not edit, merge into or push `main`
  without explicit owner authorization.
- Preserve the clean sans-serif Papertrail frontend and existing API contracts.
- Prefer one upload and one final result for composed PDF workflows.
- Keep document retention and resource-limit disclosures accurate.
- Do not claim zero temporary writes, under-50-MB memory or sub-second startup
  without reproducible evidence covering the complete gateway and worker.
- Validate relevant behavior before shipping; report unverified deployment or
  performance requirements explicitly.
