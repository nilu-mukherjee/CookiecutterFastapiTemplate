#!/usr/bin/env python
"""Generate the gRPC stubs in app/grpc/generated from proto/items/v1/items.proto.

Run ``python scripts/gen_proto.py`` after changing the proto file and commit the result.
A test regenerates the stubs into a temporary directory and fails if they differ, so the
committed stubs always match the proto and the pinned grpcio-tools.
"""
import re
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto" / "items" / "v1"
PROTO_FILE = "items.proto"
OUT_DIR = ROOT / "app" / "grpc" / "generated"


def generate(out_dir: Path) -> None:
    """Write items_pb2.py, items_pb2.pyi and items_pb2_grpc.py into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    code = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{PROTO_DIR}",
            f"-I{Path(protoc.__file__).parent / '_proto'}",  # google/protobuf/*.proto
            f"--python_out={out_dir}",
            f"--pyi_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            PROTO_FILE,
        ]
    )
    if code != 0:
        raise SystemExit(f"protoc failed with exit code {code}")
    # protoc imports its sibling as a top-level module; make it a package import.
    grpc_module = out_dir / "items_pb2_grpc.py"
    grpc_module.write_text(
        re.sub(
            r"^import items_pb2 as ",
            "from app.grpc.generated import items_pb2 as ",
            grpc_module.read_text(),
            flags=re.MULTILINE,
        )
    )


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR)
