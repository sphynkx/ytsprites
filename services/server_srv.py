import logging
from concurrent import futures
import grpc
from config.service_cfg import cfg
from proto import ytsprites_pb2_grpc
from .handlers_srv import SpritesService

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
    ytsprites_pb2_grpc.add_SpritesServicer_to_server(SpritesService(), server)
    
    address = f'[::]:{cfg.GRPC_PORT}'
    server.add_insecure_port(address)
    
    print(f"[Server] Starting on {address}")
    print(f"[Server] Max message size set to: {max_msg_size / 1024 / 1024:.2f} MB")
    
    server.start()
    server.wait_for_termination()