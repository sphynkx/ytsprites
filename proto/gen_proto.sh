#!/usr/bin/env bash
set -euo pipefail

# Generate stubs from ytsprites.proto
# Make sure that ytsprites.proto is identical to one from yurtube service!!
# And ytstorage.proto is identical to one from ytstorage service!!

cd "$(dirname "$0")"

source ../.venv/bin/activate 

python -m grpc_tools.protoc \
  -I . \
  --python_out=. \
  --grpc_python_out=. \
  ytsprites.proto

sed -i 's/^import ytsprites_pb2 as ytsprites__pb2/from . import ytsprites_pb2 as ytsprites__pb2/' ytsprites_pb2_grpc.py

echo "Generated: ytsprites_pb2.py ytsprites_pb2_grpc.py in $(pwd)"

##############

python -m grpc_tools.protoc \
  -I . \
  --python_out=. \
  --grpc_python_out=. \
  ytstorage.proto

sed -i 's/^import ytstorage_pb2 as ytstorage__pb2/from . import ytstorage_pb2 as ytstorage__pb2/' ytstorage_pb2_grpc.py

echo "Generated: ytstorage_pb2.py ytstorage_pb2_grpc.py in $(pwd)"

##############
python -m grpc_tools.protoc \
  -I . \
  --python_out=. \
  --grpc_python_out=. \
  info.proto

sed -i 's/^import info_pb2 as info__pb2/from . import info_pb2 as info__pb2/' info_pb2_grpc.py

echo "Generated: info_pb2.py info_pb2_grpc.py in $(pwd)"
