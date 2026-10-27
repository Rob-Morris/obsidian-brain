"""Maintenance readers retain public-source guards without widening imports."""

import builtins
from pathlib import Path

import pytest

import read
from _common._source_access import SourceAccessError


@pytest.mark.parametrize("resource", ["file", "memory"])
def test_legacy_readers_refuse_private_source_before_open(tmp_path, monkeypatch, resource):
    private = tmp_path / ".brain/local/synthetic.md"
    private.parent.mkdir(parents=True)
    private.write_text("Synthetic sentinel")
    original = builtins.open

    def observe(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).resolve() == private:
            pytest.fail("refused private source was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", observe)
    with pytest.raises(SourceAccessError):
        if resource == "file":
            read.read_file({}, tmp_path, ".brain/local/synthetic.md")
        else:
            router = {"memories": [{"name": "example", "triggers": ["example"],
                                    "memory_doc": ".brain/local/synthetic.md"}]}
            read.read_memory(router, tmp_path, "example")
