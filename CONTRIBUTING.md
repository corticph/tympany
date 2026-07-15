# Contributing

Thanks for your interest in Tympany.

Tympany is open-source software released under the MIT License. Contributions
are welcome. A few guidelines:

- For non-trivial changes, please open an issue first describing the problem,
  the intended use case, and the scope of the proposed fix.
- Keep pull requests focused — one concern per PR.
- All contributions are made under the MIT License.

## What to include in an issue

- A clear problem statement
- Steps to reproduce, if this is a bug
- Expected and actual behavior
- Relevant screenshots, logs, or sample inputs when safe to share
- Why the change matters for your workflow

## Security

Do not open public issues containing credentials, PHI, PII, or customer data.
If you need to report a sensitive issue, contact Corti privately at
help@corti.ai.

## Development notes

For internal contributors working from a local checkout:

```sh
poetry install
poetry run pytest
poetry run tympany-serve
```

See [README.md](README.md) for source-setup, runtime, and deployment details.
