# Dependency reproducibility

`dependencies/pyproject.toml` owns package intent for base, semantic and dev
sets, including the exact uv generator pin. Its `[tool.uv]` table declares the
virtual project and required environments: uv rejects these project-only
settings in `uv.toml`. The explicitly selected `dependencies/uv.toml` owns the
PyPI registry policy. Root `pyproject.toml` contains test/lint configuration.

One committed `dependencies/uv.lock` supplies four complete, exact,
marker-aware exports. `make install` uses `requirements-dev.txt`;
`make install-semantic` uses `requirements-dev-semantic.txt`. Both live in
`dependencies/`. Installed Brains consume `brain_mcp/requirements.txt` and
the complete optional `brain_mcp/requirements-semantic.txt`. Every installer
uses `pip --no-deps -r`; bootstrap Python and pip itself are outside the
application dependency-version guarantee. Users do not need uv.

## Contributor commands

```bash
make install
make install-semantic                       # optional
make dependencies-check                    # offline, empty-cache freshness
.venv/bin/python src/scripts/dependencies.py update
.venv/bin/python src/scripts/dependencies.py update --upgrade-package anyio
.venv/bin/python src/scripts/dependencies.py update --upgrade
```

`update` is an intentional online operation. Without upgrade flags it retains
existing selections where possible; the other modes intentionally select new
versions. Review the manifest, lock and all four exports together, run
`make install-semantic`, `make lint` and `make test`, then obtain native
certification before release. Do not edit exports by hand. If a previously
extended contributor environment fails `pip check`, preserve it and recreate
`.venv` from the committed exports.

The wrapper reads the generator version from the sole authored uv pin and
selects `uv.toml` explicitly. It removes all ambient `UV_*` values, disables
Python downloads and source builds, and rejects non-PyPI lock sources.
`check` runs `uv lock --check --offline` followed by locked offline exports to
a disposable directory and byte comparisons. An empty registry cache is used;
missing tooling or declaration drift fails with setup/update guidance.
Repository contracts run this against both checkout and materialised staged
code. `.gitattributes` protects every dependency input/output from CRLF drift.

## Native release certification

The dedicated `Dependency certification` workflow runs CPython 3.12 on
`macos-15` (arm64), `ubuntu-24.04` (x86_64) and `windows-2025` (x86_64).
These labels use [GitHub's native hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Each leg records the concrete OS and Python version, creates a clean runtime,
installs base wheels with `--only-binary=:all: --no-deps`, verifies every applicable pin, runs
`pip check` and a real MCP read, then extends it with semantic wheels and runs
the pinned model and semantic ranking smoke. The shared base versions must
remain equal. Repository contracts check this installation boundary in the
certification script itself. Model provisioning needs network access to Hugging Face.

The final `certified` job requires every native leg to succeed. A missing,
cancelled, skipped or failed leg means the release is **not certified**;
cross-target wheel resolution and a local smoke cannot substitute for it.
Require that job in repository release policy. The ordinary Linux full suite
and Windows user smoke remain separate signals.

This guarantees selected package versions, not byte-identical environments,
package hashes, permanent download availability, offline installs or identical
OS/Python patches. The current semantic pins need macOS 14+ arm64 and glibc
2.28+ Linux wheels; ONNX Runtime currently has no Intel macOS wheel. Other
admitted Python 3.12+ environments remain best-effort with explicit failures.

See [DD-077](../architecture/decisions/dd-077-dependency-reproducibility.md)
for managed-runtime identity and readiness semantics.
