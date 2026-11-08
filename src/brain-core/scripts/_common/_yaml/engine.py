"""Public YAML API; implementation is safe during Core replacement."""

from _bootstrap.yaml_engine import (
    YamlError, dump_yaml_text, load_yaml_file, load_yaml_text,
)

__all__ = ["YamlError", "dump_yaml_text", "load_yaml_file", "load_yaml_text"]
