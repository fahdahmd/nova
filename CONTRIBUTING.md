# Contributing

Thanks for taking an interest. Nova is a local-first assistant, so the bar for
anything that touches the network, the filesystem or subprocesses is high.

## Getting set up

```bash
uv pip install -e ".[dev]"
pre-commit install
```

## Before opening a pull request

```bash
ruff check src tests scripts
ruff format src tests scripts
mypy
bandit -c pyproject.toml -r src
pip-audit
pytest
```

CI runs exactly these on Python 3.11, 3.12 and 3.13.

## House rules

- **Commits** use [Conventional Commits](https://www.conventionalcommits.org):
  `type(scope): short imperative summary`, with types `feat`, `fix`, `docs`,
  `test`, `chore`, `refactor`, `ci`, `build`, `perf`. Keep them small and
  logical.
- **Types** — the codebase is `mypy --strict` clean. Keep it that way.
- **New tools** need a pydantic argument model, a risk tier, a short description
  (under ~100 characters, since a 4B model has to choose from it) and tests.
- **Anything with a side effect** that is not read-only and local must be
  `sensitive` or `destructive`, so the user is asked first. Never add a path that
  lets the model approve its own action.
- **Filesystem access** goes through `PathSandbox`. Do not add a second way in.
- **Subprocesses** use argument lists from the config whitelist. No `shell=True`,
  ever.
- **External content** (files, web results) must be wrapped with
  `wrap_untrusted()` before it reaches the model.
- **No new runtime dependencies** without a good reason, and nothing that phones
  home.
- **The UI** is plain HTML, CSS and ES modules with no build step. It must keep
  working under the strict CSP, which means no inline scripts and no `innerHTML`.
- **No AI attribution** in commits, code or docs.

## Reporting bugs

Please include your OS, Python version, the model you are using, and the relevant
lines from the console. If it involves a tool call, the Activity tab in the UI
shows exactly what ran.

## Security

Do not open a public issue for a vulnerability — see [SECURITY.md](SECURITY.md).
