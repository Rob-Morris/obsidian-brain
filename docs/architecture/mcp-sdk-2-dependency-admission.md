# MCP SDK 2.0 Dependency Admission

This record admits the official Python MCP SDK 2.0.0 into the Brain managed
runtime. The application command layer remains independent of MCP and Pydantic;
the dependency is confined to `brain_mcp` and its transport adapters.

## Decision

Pin `mcp==2.0.0`. Do not use a broad major-version range and do not adopt the
separately versioned standalone FastMCP package as a substitute. Re-run this
admission review before changing the pin.

The reviewed package is the stable, non-yanked MIT-licensed release published
as `mcp` by the Model Context Protocol project. The resolved wheel has SHA-256
`1cb4c75d2d2c7b8c1d756355e5d82a39f2822cc7f13e22a2051d7ca3592349d6`.
Its package metadata requires Python 3.10 or later; Brain continues to require
Python 3.12 or later.

## Resolution evidence

On 2026-08-12, pip 26.2 resolved the pin for CPython 3.12 on macOS arm64,
manylinux x86-64, and Windows amd64. All three targets resolved this common
graph; Windows additionally resolves `pywin32==312`:

| Package | Version | Licence |
| --- | --- | --- |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.14.2 | MIT |
| attrs | 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| click | 8.4.2 | BSD-3-Clause |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.10.0 | BSD-3-Clause |
| httpx2 | 2.10.0 | BSD-3-Clause |
| idna | 3.18 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| mcp | 2.0.0 | MIT |
| mcp-types | 2.0.0 | MIT |
| opentelemetry-api | 1.44.0 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic-core | 2.46.4 | MIT |
| pyjwt | 2.13.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.6.3 | MIT |
| sse-starlette | 3.4.8 | BSD-3-Clause |
| starlette | 1.6.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typing-inspection | 0.4.3 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.52.1 | BSD-3-Clause |
| pywin32 (Windows only) | 312 | PSF-2.0 |

Platform-specific native wheels were resolved rather than silently borrowing
the host wheel. Their SHA-256 hashes were:

| Package | macOS arm64 | manylinux x86-64 | Windows amd64 |
| --- | --- | --- | --- |
| cffi 2.1.1 | `f81b3b8f3d4e343550fa4baa0e479bba9f2d29ce9c2e9b51d1ce1718d7442fcf` | `c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf` | `f53e442b08449d42821fa4a4fba000095af9f62742a500f978a9f557ec44339a` |
| cryptography 50.0.0 | `031e2d5dd4bb9caa3ca9c82e5a197fd8ae680232cee62603d1a813f3f07e3d03` | `06a32a980526a6ab9a4b9bf8f7385800791e2bb960903cb6b530e4817509a3b7` | `bd1c592e4d5974f0d08d4888e432157adba757c66da0246918e43677fafa2d30` |
| pydantic-core 2.46.4 | `962ccbab7b642487b1d8b7df90ef677e03134cf1fd8880bf698649b22a69371f` | `926c9541b14b12b1681dca8a0b75feb510b06c6341b70a8e500c2fdcff837cce` | `e9c26f834c65f5752f3f06cb08cb86a913ceb7274d0db6e267808a708b46bc89` |
| rpds-py 2026.6.3 | `538949e262e46caa31ac01bdb3c1e8f642622922cacbabbae6a8445d9dc33eaf` | `ecabd69db66de867690f9797f2f8fa27ba501bbc24540cbdbdc649cd15888ba6` | `2c958bf94822e9290a40aaf2a822d4bc5c88099093e3948ad6c571eca9272e5f` |

The Windows-only `pywin32==312` wheel hash is
`b457f6d628a47e8a7346ce22acb7e1a46a4a78b52e1d17e1af56871bd19a93bc`.

Relative to `mcp==1.29.0`, the resolved macOS graph decreases from 29 to 28
packages. It adds `httpcore2`, `httpx2`, `mcp-types`, `opentelemetry-api`, and
`truststore`; it removes `certifi`, `httpcore`, `httpx`, `httpx-sse`,
`pydantic-settings`, and `python-dotenv`.

## Security and runtime policy

An isolated `pip-audit --strict` run reported no known vulnerability in the
resolved MCP runtime graph. Its only finding was the temporary environment's
bootstrap `pip==25.0.1`; Brain's managed-runtime provisioner upgrades pip before
installing requirements, so that package is not accepted as part of this graph.

The SDK installs the OpenTelemetry API and a default middleware, but does not
install the OpenTelemetry SDK or an exporter. Brain retains that non-recording,
non-exporting default and does not configure telemetry through MCP. Brain-owned
operational logging remains authoritative. Adding an SDK or exporter requires a
new privacy and data-egress review.

`httpx2` delegates certificate verification to the operating-system trust store
through `truststore`, replacing the previous `certifi` path. This is suitable for
the local stdio server and for platform-managed HTTPS trust. A future remote
Brain client must make private-CA configuration explicit rather than weakening
verification.

The graph is sourced from PyPI wheels over HTTPS with package-record hashes.
All declared licences are permissive. The managed runtime remains the only
runtime installation owner, and application modules must continue to pass the
import-boundary tests that prohibit importing `mcp` or `pydantic`.

## Verification gate

Admission is complete only when installation, import-boundary, MCP contract,
proxy drift, and supported-platform tests pass with this exact pin. A passing
dependency review does not waive those behavioural gates.
