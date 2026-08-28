from __future__ import annotations


# The Brain Lab image owns this interpreter. Never resolve lab helpers through
# the retained run's mutable PATH, which starts with /home/brain/.local/bin.
CONTAINER_PYTHON = "/usr/bin/python3.12"
