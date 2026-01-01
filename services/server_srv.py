import logging
from concurrent import futures
import grpc
from grpc_reflection.v1alpha import reflection
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from config.service_cfg import cfg
from proto import ytsprites_pb2_grpc, info_pb2_grpc
from .handlers_srv import SpritesService
from .info_srv import InfoService

def serve():
    # Calc size, + add some extra.
    max_msg_size = (cfg.MAX_VIDEO_SIZE_MB + 5) * 1024 * 1024
    options = [
        ('grpc.max_receive_message_length', max_msg_size),
        ('grpc.max_send_message_length', max_msg_size),
    ]
    
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10), 
        options=options
    )
    
    # Base service
    ytsprites_pb2_grpc.add_SpritesServicer_to_server(SpritesService(), server)
    
    # Health service
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    
    # Info service
    info_servicer = InfoService()
    info_pb2_grpc.add_InfoServicer_to_server(info_servicer, server)
    
    # Reflection
    service_names = (
        ytsprites_pb2_grpc.SpritesServicer.__module__.split(".")[1],
        health_pb2_grpc.HealthServicer.__module__.split(".")[1],
        info_pb2_grpc.InfoServicer.__module__.split(".")[1],
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    
    address = f'[::]:{cfg.GRPC_PORT}'
    server.add_insecure_port(address)
    
    print(f"[Server] Starting on {address}")
    print(f"[Server] Max message size set to: {max_msg_size / 1024 / 1024:.2f} MB")
    print(f"[Server] Reflection enabled. Services: {service_names}")
    
    server.start()
    server.wait_for_termination()