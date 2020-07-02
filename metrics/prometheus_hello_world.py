#!/usr/bin/env python3

from prometheus_client import start_http_server, Summary, ProcessCollector, REGISTRY, PROCESS_COLLECTOR, PlatformCollector, PLATFORM_COLLECTOR
import random
import time

REGISTRY.unregister(PROCESS_COLLECTOR)
ProcessCollector(namespace='helloworld')
REGISTRY.unregister(PLATFORM_COLLECTOR)
PlatformCollector(namespace='helloworld')

# Create a metric to track time spent and requests made.
REQUEST_TIME = Summary('helloworld_request_processing_seconds', 'Time spent processing request')

# Decorate function with metric.
@REQUEST_TIME.time()
def process_request(t):
    """A dummy function that takes some time."""
    time.sleep(t)

if __name__ == '__main__':
    # Start up the server to expose the metrics.
    start_http_server(8000)
    # Generate some requests.
    while True:
        process_request(random.random())
