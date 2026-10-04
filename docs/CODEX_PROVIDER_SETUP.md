# Codex Provider Setup

PaperFit's Codex adapter does not require `api.openai.com` specifically.

## Principle

- PaperFit installs the discoverable paperfit skill and optional project rules; see [host workflow](HOST_NATIVE_WORKFLOW.md).
- PaperFit does **not** overwrite `~/.codex/config.toml`.
- Codex model provider, base URL, auth mode, and model choice remain under the user's control.

That means PaperFit can run on:

- the user's default Codex login flow
- a third-party OpenAI-compatible gateway
- a self-hosted proxy or enterprise relay

as long as the local `codex` CLI itself is already configured and usable.

## Recommended Strategy

Use the user's existing Codex setup as the primary path.

PaperFit should be layered on top of:

1. a working `codex` CLI
2. a valid `~/.codex/config.toml` when custom providers are needed
3. `paperfit install-global --target codex`

## Example Third-Party Provider

```toml
model_provider = "mygateway"
model = "gpt-5.4"

[model_providers.mygateway]
name = "mygateway"
base_url = "https://your-openai-compatible-gateway.example.com"
wire_api = "responses"
requires_openai_auth = true
```

Adjust auth and endpoint settings to match the actual gateway you use.

## Verification

Run:

```bash
paperfit doctor --target codex
```

PaperFit will report:

- whether Codex assets were installed
- whether the installed paperfit skill has discovery metadata
- whether its receipt points to an available tool package

## Boundary

If Codex cannot reach its configured backend, that is a Codex/provider connectivity issue, not a PaperFit asset-install issue.
