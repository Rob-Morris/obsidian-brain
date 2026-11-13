from pathlib import Path
from types import SimpleNamespace

import pytest

from brain_lab.docker import DockerClient
from brain_lab.resources import build_base
from brain_lab.store import StateStore


TOOL_ROOT = Path(__file__).resolve().parents[3] / "tools" / "brain-lab"


class BaseDocker:
    def __init__(self):
        self.pulls = []
        self.builds = []
        self.images = {}

    def pull(self, image, platform, evidence_directory, **kwargs):
        self.pulls.append((image, platform))

    def image_inspect(self, reference, evidence_directory):
        if reference == "ubuntu:24.04":
            return {"Id": "sha256:upstream", "RepoDigests": ["ubuntu@sha256:reviewed"]}
        return self.images[reference]

    def build(self, dockerfile, **kwargs):
        self.builds.append(kwargs)
        self.images[kwargs["tag"]] = {
            "Id": f"sha256:built-{len(self.builds)}",
            "Config": {"Labels": kwargs["labels"]},
            "Architecture": "arm64", "Os": "linux",
        }

    verify_resource_labels = staticmethod(DockerClient.verify_resource_labels)


def test_new_refresh_key_creates_a_new_base_without_overwriting_history(tmp_path):
    context = SimpleNamespace(
        docker=BaseDocker(), store=StateStore(tmp_path / "state"),
        evidence_directory=tmp_path / "evidence", tool_root=TOOL_ROOT,
    )
    original = build_base(context, {})
    original_id = original.resource["id"]
    original_receipt = context.store.read("base", original_id)
    first = build_base(context, {"package_refresh": "review-a"})
    first_id = first.resource["id"]
    first_receipt = context.store.read("base", first_id)
    repeated = build_base(context, {"package_refresh": "review-a"})
    second = build_base(context, {"package_refresh": "review-b"})

    assert len({original_id, first_id, second.resource["id"]}) == 3
    assert repeated.resource["id"] == first_id
    assert repeated.payload["reused"] is True
    assert len(context.docker.builds) == 3
    assert context.docker.builds[1]["build_arguments"] == {
        "BASE_IMAGE": "ubuntu@sha256:reviewed", "PACKAGE_REFRESH": "review-a",
    }
    assert context.docker.builds[2]["build_arguments"]["PACKAGE_REFRESH"] == "review-b"
    assert context.store.read("base", original_id) == original_receipt
    assert context.store.read("base", first_id) == first_receipt


@pytest.mark.parametrize("value", ["", "  ", False, 1, [], {}])
def test_invalid_refresh_key_is_refused_before_docker_io(tmp_path, value):
    docker = BaseDocker()
    context = SimpleNamespace(docker=docker)
    with pytest.raises(ValueError, match="package_refresh"):
        build_base(context, {"package_refresh": value})
    assert docker.pulls == []
    assert docker.builds == []
