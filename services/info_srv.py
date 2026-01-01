import time
import socket
from proto import info_pb2, info_pb2_grpc
from config.service_cfg import cfg

class InfoService(info_pb2_grpc.InfoServicer):
    def __init__(self):
        self.start_time = time.time()
        self.instance_id = socket.gethostname()
        self.host = f"{socket.gethostbyname(socket.gethostname())}:{cfg.GRPC_PORT}"

    def All(self, request, context):
        uptime = time.time() - self.start_time
        response = info_pb2.InfoResponse(
            app_name="YTSprites-srv",
            instance_id=self.instance_id,
            host=self.host,
            version="1.0.0",
            uptime=int(uptime),
            labels={"env": "production"},
            metrics={"uptime_sec": uptime},
        )
        return response