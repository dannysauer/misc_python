#!/usr/bin/env python3

import docker
from time import sleep

client = docker.from_env()
containers = []
for i in range(1,50):
    host = f"stresshost_{i}"
    cont = client.containers.run(
        "busybox",
        "/bin/httpd -fh /etc",
        hostname=host,
        name=host,
        remove=True,
        detach=True,
        )
    containers.append(cont)

sleep(5)
for cont in containers:
    cont.kill()
    #cont.remove()
