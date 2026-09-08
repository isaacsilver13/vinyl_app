---
description: Prepare or verify a Vinyl app/API release with status-first checks and explicit deployment confirmation.
---

Work on the requested Vinyl release or deployment as a controlled workflow.

1. Identify whether the change belongs to `repos/vinyl_app`, `repos/vinyl_api`, or both. Confirm the current branch, remote, Fly app name, and changed files before operating.
2. Run non-destructive status and health checks first. The app check targets the Streamlit root; the API check targets `/health`. Do not invent a health endpoint.
3. Inspect `fly.toml` and the relevant GitHub workflow. Preserve GitHub Actions as the normal push-to-main deployment path.
4. Before a manual deployment, state the exact service, Fly app, branch/commit, config path, and production impact. Require explicit confirmation. Never request or print secrets.
5. Use the canonical `repos/vinyl_app/scripts/deploy_status.ps1` wrapper when appropriate. It defaults to status/health checks; `-Deploy` is opt-in and must not be run implicitly.
6. After an approved deployment, verify Fly status and the public health surface, then report manual checks that remain: login, API-backed collection access, refresh behavior, OAuth, email alerts, or scheduled jobs as applicable.
7. Report confirmed results, failed checks, and unverified assumptions separately. Do not push, merge, or mutate secrets as part of this prompt.
