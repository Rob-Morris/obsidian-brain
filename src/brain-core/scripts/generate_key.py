#!/usr/bin/env python3
"""
generate_key.py — Generate an operator key for brain-core config.

Produces a three-word key (e.g. "timber-compass-violet") and its SHA-256
hash. Give the words to the agent operator; paste the hash into config.yaml.

Usage:
    python3 generate_key.py
    python3 generate_key.py --count 3   # generate multiple candidates
"""

import argparse
import secrets

from config import hash_key

_WORDS = [
    "amber", "anchor", "anvil", "apple", "arrow", "aspen",
    "basin", "beacon", "birch", "blade", "blaze", "bloom",
    "boulder", "bridge", "brook", "buoy",
    "cabin", "cedar", "chalk", "chart", "chime", "cipher",
    "clay", "cliff", "cloak", "cloud", "clover", "cobalt",
    "comet", "compass", "copper", "coral", "crest", "crown",
    "crystal",
    "dagger", "delta", "depot", "depth", "dew", "dome",
    "drift", "dune",
    "ebony", "echo", "ember", "epoch",
    "falcon", "fern", "field", "flare", "flint", "flume",
    "fog", "forge", "frost",
    "garnet", "gate", "glade", "glint", "glyph", "grain",
    "granite", "grove",
    "hammer", "harbor", "hatch", "haven", "hawk", "haze",
    "helm", "hollow", "husk",
    "ice", "indigo", "inlet", "iron", "isle", "ivory",
    "jasper", "jetty",
    "kelp", "kestrel",
    "latch", "laurel", "ledge", "lime", "linden", "loch",
    "loom", "lunar",
    "maple", "marble", "marsh", "mast", "mesa", "mire",
    "mist", "moat", "mortar", "moss", "mural",
    "needle", "north",
    "oak", "oar", "obsidian", "ochre", "onyx", "orbit", "ore",
    "parch", "peak", "pine", "pixel", "plank", "plume",
    "poplar", "prism", "pulsar",
    "quartz",
    "raven", "reed", "reef", "ridge", "rift", "rim", "ripple",
    "rock", "rune",
    "saddle", "sage", "salt", "shard", "shale", "shell",
    "shore", "signal", "silver", "slate", "smoke", "solar",
    "spark", "spire", "spool", "spring", "spruce", "stone",
    "storm", "strata", "stream", "summit",
    "thorn", "tide", "timber", "torch", "trace", "trail",
    "trench", "tundra",
    "vale", "vault", "veil", "vent", "vine", "violet",
    "wake", "wave", "wax", "web", "wedge", "willow",
    "wind", "wire", "wolf",
    "zinc",
]


def generate_key() -> str:
    return "-".join(secrets.choice(_WORDS) for _ in range(3))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an operator key for brain-core config."
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=1,
        metavar="N",
        help="Number of key candidates to generate (default: 1)",
    )
    args = parser.parse_args()

    for i in range(args.count):
        if i > 0:
            print()
        key = generate_key()
        print(f"Key:  {key}")
        print(f"Hash: {hash_key(key)}")


if __name__ == "__main__":
    main()
