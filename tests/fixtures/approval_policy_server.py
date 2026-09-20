"""Harmless stdio tools for native host-approval certification (no vault access)."""

import json
import sys


TOOLS = ("artefact_read", "document_structured-edit", "access_request")


def main():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "approval-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": "Return a harmless approval-test marker.",
                                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
                                for name in TOOLS]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "APPROVAL_FIXTURE_CALLED:" + request["params"]["name"]}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
