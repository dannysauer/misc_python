#!/usr/bin/env python3

import argparse
import docker
import requests
from time import sleep

parser = argparse.ArgumentParser()
parser.add_argument(
    "number",
    help="number of instances (defaults to 1)",
    type=int,
    default=1,
    nargs='?', # make this optional
    )
parser.add_argument(
    "-v", "--verbose",
    help="Print more debug output",
    action="count",
    default="0",
    )
args = parser.parse_args()
target_instances = args.number

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

for cont in containers:
    print(f"Verifying {cont.name}")
    eport = cont.attrs['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
    #print(f"Container {cont.name} exposes port {eport}")
    url=f"http://localhost:{eport}/hostname"
    r = requests.get(url)
    print(f"{url} -> '{r.text.rstrip()}'")
    print(f"Cleaning up {cont.name}")
    cont.kill()
    #cont.remove()
