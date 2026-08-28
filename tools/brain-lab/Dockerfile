ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

COPY container/package_inputs.py /usr/local/lib/brain-lab/package_inputs.py

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        python3 \
        python3.12 \
        python3.12-venv \
        rsync \
    && python3 /usr/local/lib/brain-lab/package_inputs.py \
        --output /usr/local/share/brain-lab/package-inputs.json \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 brain \
    && useradd --uid 10001 --gid brain --create-home --shell /bin/bash brain \
    && install -d -o brain -g brain \
        /home/brain/.brain \
        /home/brain/.config \
        /home/brain/.local \
        /home/brain/.local/bin \
        /home/brain/.local/share \
        /home/brain/work

COPY container/mcp_probe.py /usr/local/lib/brain-lab/mcp_probe.py
COPY container/active_path_probe.py /usr/local/lib/brain-lab/active_path_probe.py
COPY container/clear_imported_state.py /usr/local/lib/brain-lab/clear_imported_state.py
COPY container/pin_mcp_requirement.py /usr/local/lib/brain-lab/pin_mcp_requirement.py
COPY container/restore_seed_paths.py /usr/local/lib/brain-lab/restore_seed_paths.py
COPY container/tree_manifest.py /usr/local/lib/brain-lab/tree_manifest.py
COPY container/changed_content.py /usr/local/lib/brain-lab/changed_content.py
COPY container/historical_upgrade_acceptance.py /usr/local/lib/brain-lab/historical_upgrade_acceptance.py

ENV HOME=/home/brain \
    XDG_CONFIG_HOME=/home/brain/.config \
    XDG_DATA_HOME=/home/brain/.local/share \
    PATH=/home/brain/.local/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER brain
WORKDIR /home/brain/work
