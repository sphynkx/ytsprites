import logging
from services import server_srv, worker_srv

def main():
    logging.basicConfig(level=logging.INFO)
    
    print("Starting ytsprites service...")
    
    worker_srv.start_workers()
    
    try:
        server_srv.serve()
    except KeyboardInterrupt:
        print("Stopping...")

if __name__ == '__main__':
    main()