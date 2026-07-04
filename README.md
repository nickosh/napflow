# napflow

Local-first, git-friendly, node-based flow editor and engine for complex
API request/response processing — "Postman Flows, but open, file-based,
Python-powered, and composable."

**Status: pre-alpha.** Nothing is usable yet; S1 (loader, models,
`napf check`) is in progress. See [docs/PLAN.md](docs/PLAN.md) for the
roadmap and [docs/](docs/) for the authoritative specs.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run ruff check
uv run lint-imports
```

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
