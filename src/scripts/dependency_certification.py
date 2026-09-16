#!/usr/bin/env python3
"""Native release evidence: clean wheel installs, conformance and real smoke.

Every failure is fatal. This command has no skip or cross-target fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src/brain-core"
sys.path.insert(0, str(CORE / "scripts"))
sys.path.insert(0, str(CORE))


async def mcp_smoke(vault: Path) -> None:
    """Start the actual MCP server and complete a granular read."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, BRAIN_VAULT_ROOT=str(vault), PYTHONPATH=str(vault / ".brain-core"))
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "brain_mcp.proxy",
                                         sys.executable, "brain_mcp.server"],
                                   env=env, cwd=vault)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.call_tool("runtime_read-environment", {})
            if result.is_error or result.structured_content["status"] != "ok":
                raise RuntimeError(f"MCP smoke failed: {result}")


def install_command(python: Path, export: Path) -> list[str]:
    """Build the non-resolving, wheel-only certification install command."""
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--only-binary=:all:",
        "--no-deps",
        "-r",
        str(export),
    ]


def smoke(vault: Path, semantic: bool) -> None:
    """Exercise imports, MCP and the real pinned encoder/search implementation."""
    asyncio.run(mcp_smoke(vault))
    if semantic:
        import numpy as np
        from _semantic.model import provision_semantic_model, get_query_encoder
        from _search.semantic_query import search_semantic

        provision_semantic_model(vault)
        encoder = get_query_encoder(vault)
        texts = ["Apples and oranges are fruit grown in an orchard.",
                 "A mechanic repairs car engines in a garage."]
        vectors = encoder.encode(texts, normalize_embeddings=True)
        if vectors.shape != (2, 384) or not np.isfinite(vectors).all():
            raise RuntimeError("semantic encoder produced invalid vectors")
        documents = [{"path": f"Notes/{i}.md", "title": text, "type": "living/note"}
                     for i, text in enumerate(texts)]
        results = search_semantic("fruit trees in an orchard", vault,
                                  doc_embeddings=vectors, embeddings_meta={"documents": documents},
                                  query_encoder=encoder, attach_snippets_to_results=False)
        if not results or results[0]["path"] != "Notes/0.md":
            raise RuntimeError(f"semantic search ranked the wrong document: {results}")


def main() -> None:
    """Certify the actual interpreter/host, then run both clean-runtime phases."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform")
    parser.add_argument("--machine")
    parser.add_argument("--smoke", choices=("base", "semantic"))
    parser.add_argument("--vault", type=Path)
    args = parser.parse_args()
    if args.smoke:
        smoke(args.vault, args.smoke == "semantic")
        return
    if (platform.python_implementation(), sys.version_info[:2], sys.platform, platform.machine()) != (
        "CPython", (3, 12), args.platform, args.machine
    ):
        raise RuntimeError("certification requires the declared native CPython 3.12 target")
    print(json.dumps({"os": platform.platform(), "uname": platform.uname()._asdict(),
                      "python": sys.version, "runner_image": os.environ.get("ImageVersion")}), flush=True)
    from _common._venv import conform_runtime, venv_dir_for

    with tempfile.TemporaryDirectory(prefix="brain-certification-") as temporary:
        root = Path(temporary).resolve()
        vault = root / "vault"
        home = root / "home"
        home.mkdir()
        env = dict({key: value for key, value in os.environ.items() if not key.startswith("BRAIN_")},
                   HOME=str(home), USERPROFILE=str(home),
                   APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"))
        for key in tuple(os.environ):
            if key.startswith("BRAIN_"):
                del os.environ[key]
        os.environ.update(env)
        base = CORE / "brain_mcp/requirements.txt"
        runtime = venv_dir_for(base)
        subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
        python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        shutil.copytree(ROOT / "template-vault", vault, ignore=shutil.ignore_patterns(".brain-core", ".git", ".DS_Store"))
        shutil.copytree(CORE, vault / ".brain-core", ignore=shutil.ignore_patterns("__pycache__"))
        for selected in ("base", "semantic"):
            export = CORE / "brain_mcp" / ("requirements.txt" if selected == "base" else "requirements-semantic.txt")
            subprocess.run(install_command(python, export), check=True)
            conform_runtime(python, base, semantic=selected == "semantic")
            subprocess.run([str(python), __file__, "--smoke", selected, "--vault", str(vault)], check=True, env=env)
            print(f"{selected}: certified", flush=True)


if __name__ == "__main__":
    main()
