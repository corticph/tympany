# Contributing

Thanks for your interest in Tympany.

This repository is publicly visible so teams can evaluate and run the software,
but Tympany is currently released under a proprietary source-available license,
not an open-source license. That has a few practical consequences:

- External pull requests, forks, patches, and derivative works are not accepted
  under an implied open-source contribution model.
- If you want to propose a change, please open an issue first describing the
  problem, the intended use case, and the scope of the proposed fix.
- If Corti wants to accept an external contribution, we will coordinate the
  licensing and contribution terms explicitly before code is merged.

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
