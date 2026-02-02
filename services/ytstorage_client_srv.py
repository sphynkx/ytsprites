import asyncio
from typing import AsyncIterator, Optional, List
import grpc
from services.ytstorage.ytstorage_proto import ytstorage_pb2 as spb
from services.ytstorage.ytstorage_proto import ytstorage_pb2_grpc as sgrpc


def _auth_md(token: str) -> List[tuple]:
    tok = (token or "").strip()
    if not tok:
        return []
    return [("authorization", f"Bearer {tok}")]


def make_channel(address: str, *, tls: bool = False, max_msg_mb: int = 256) -> grpc.aio.Channel:
    max_msg = int(max_msg_mb) * 1024 * 1024
    opts = [
        ("grpc.max_send_message_length", max_msg),
        ("grpc.max_receive_message_length", max_msg),
    ]
    if tls:
        creds = grpc.ssl_channel_credentials()
        return grpc.aio.secure_channel(address, creds, options=opts)
    return grpc.aio.insecure_channel(address, options=opts)


class YtStorageClient:
    def __init__(self, address: str, *, token: str = "", tls: bool = False) -> None:
        self.address = address
        self.token = token
        self.tls = tls
        self.channel = make_channel(address, tls=tls)
        self.stub = sgrpc.StorageServiceStub(self.channel)

    async def close(self) -> None:
        await self.channel.close()

    async def stat(self, rel_path: str) -> spb.StatResponse:
        return await self.stub.Stat(spb.StatRequest(path=spb.Path(rel_path=rel_path)), metadata=_auth_md(self.token))

    async def mkdirs(self, rel_path: str, exist_ok: bool = True) -> None:
        await self.stub.Mkdirs(spb.MkdirsRequest(path=spb.Path(rel_path=rel_path), exist_ok=exist_ok), metadata=_auth_md(self.token))

    async def read(self, rel_path: str, *, offset: int = 0, length: int = -1) -> AsyncIterator[bytes]:
        stream = self.stub.Read(
            spb.ReadRequest(path=spb.Path(rel_path=rel_path), offset=int(offset), length=int(length)),
            metadata=_auth_md(self.token),
        )
        async for chunk in stream:
            data = bytes(chunk.data or b"")
            if data:
                yield data

    async def write_bytes(self, rel_path: str, data_iter: AsyncIterator[bytes], *, overwrite: bool = True) -> spb.WriteAck:
        """
        Write stream to storage using bidirectional streaming API.
        We collect last ack and return it.
        """
        md = _auth_md(self.token)

        async def _producer():
            header = spb.WriteHeader(path=spb.Path(rel_path=rel_path), overwrite=overwrite, append=False, expected_size=0, etag="")
            yield spb.WriteEnvelope(header=header)
            async for chunk in data_iter:
                if not chunk:
                    continue
                yield spb.WriteEnvelope(data=spb.WriteData(data=bytes(chunk)))

        last_ack: Optional[spb.WriteAck] = None
        async for ack in self.stub.Write(_producer(), metadata=md):
            last_ack = ack
        return last_ack or spb.WriteAck(ok=False, error="no_ack", bytes_written=0)