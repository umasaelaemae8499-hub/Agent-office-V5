# Generation providers

| Provider | Status | Notes |
|---|---|---|
| ComfyUI (local) | **Implemented**; protocol covered by mocked tests; no live run in this repo yet | free, needs your workflow (see COMFYUI.md) |
| Kling | **Blocked: not implemented** | official API docs could not be verified from the build environment |
| Runway | **Blocked: not implemented** | official docs host unreachable from the build environment |

## Why cloud adapters are not implemented yet
The project rules require verified endpoints, auth, and pricing. During the build:
- `docs.dev.runwayml.com` did not resolve from the sandbox. Third-party pages describe
  `/v1/text_to_video`, `/v1/image_to_video` and `/v1/tasks/{id}` plus an `X-Runway-Version`
  header, with per-second credit pricing. These are **unverified**.
- Kling's official developer docs were not found. Community sources describe JWT (HS256)
  auth from an access/secret key pair and `/v1/videos/text2video`, with conflicting prices.
  These are **unverified**.

API access is billed separately from consumer subscriptions for both providers.

## Adding a verified adapter
Subclass `app.providers.cloud_provider.CloudJobProvider` and implement `base_url`,
`auth_headers`, `submit_request`, `parse_submit`, `status_path` and `parse_status` from the
official docs. Set `verified = True` and `cost_per_second_usd`. Register it in
`app/providers/router.py::build_providers`. The base class already handles polling,
timeouts, bounded retries, HTTPS-only downloads and cost recording.

## Spending safeguards (all must pass before any paid call)
1. `ALLOW_PAID_GENERATION=true` in `.env` (default `false`).
2. The estimate fits `min(MAX_PROJECT_BUDGET_USD, project budget) - already spent` (default cap `0`).
3. The project is explicitly approved: `python -m app.main approve --project ID --budget 5`.

Otherwise the project pauses as `blocked` with the estimated cost and the available options.
User media is never uploaded to a cloud provider unless that provider was selected under these rules.
