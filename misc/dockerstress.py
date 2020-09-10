#!/usr/bin/env python3

import docker
from time import sleep

target_instances = 5

client = docker.from_env()
containers = []
for i in range(1, target_instances + 1):
    host = f"stresshost_{i}"
    print(f"creating {host}")
    cont = client.containers.run(
        "busybox",
        "/bin/httpd -fh /etc",
        hostname=host,
        name=host,
        remove=True,
        detach=True,
        ports={"80/tcp": None}, # picks a random port
        publish_all_ports=True,
        )
    # have to get the container after it's started so things like
    # "assigned ports" are fully populated.  Otherwise we just get
    # the configuration value, which is None :/ 
    populated_cont = client.containers.get(cont.name)
    containers.append(populated_cont)

#sleep(5)
#import pprint
#pp = pprint.PrettyPrinter(indent=4)
for cont in containers:
    #pp.pprint(cont.attrs)
    populated_cont = client.containers.get(cont.name)
    eport = cont.attrs['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
    print(f"Container {cont.name} exposes port {eport}")
    cont.kill()
    #cont.remove()