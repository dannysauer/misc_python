#!/usr/bin/env python3

import argparse
import docker
import requests
import threading
from time import sleep

# seems like this is something that should be built-in :/
class Atomic_counter:
    def __init__(self, value=0):
        self._val = value
        self._lock = threading.Lock()

    def increment(self):
        with self._lock:
            self._val += 1
            return self._val
    def inc(self):
        return self.increment()
    
    # instance is the instance of this class, owner is the parent class
    def __get__(self, instance, owner):
        with self._lock:
            return self._val
    def __set__(self, instance, value):
        with self._lock:
            self._val = value
            return self._val

    def __add__(self, other):
        with self._lock:
            self._val += other
            return self._val

    def __str__(self):
        return self._val.__str__()


class Dockerstress:
    def __init__(self, target_instances=1, threads=1):
        self.target_instances = target_instances
        self.thread_count = threads

        self.client = docker.from_env()
        self.containers = []
        self.index = Atomic_counter()

    def runall(self):
        self.create_containers()
        self.verify_containers()
        self.cleanup()

    def create_containers(self):
        for i in range(1, self.target_instances + 1):
            self._create_container()

    def _create_container(self):
        i = self.index.increment()
        host = f"stresshost_{i}"
        print(f"creating {host}")
        cont = self.client.containers.run(
            "busybox",
            ["/bin/httpd", "-fh", "/etc"],
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
        self.containers.append(
            self.client.containers.get(cont.name)
            )

    def verify_containers(self):
        for cont in self.containers:
            self._verify_container(cont)

    def _verify_container(self, cont):
        print(f"Verifying {cont.name}")
        eport = cont.attrs['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
        #print(f"Container {cont.name} exposes port {eport}")
        url=f"http://localhost:{eport}/hostname"
        r = requests.get(url)
        print(f"{url} -> '{r.text.rstrip()}'")

    def cleanup(self):
        for cont in self.containers:
            print(f"Cleaning up {cont.name}")
            cont.kill()
            #cont.remove()


def main(args):
    d = Dockerstress(target_instances=args.number, threads=args.threads)
    d.runall()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "number",
        help="number of instances (defaults to 1)",
        type=int,
        default=1,
        nargs='?', # make this optional
        )
    parser.add_argument(
        "-t", "--threads",
        help="Number of creation/verification threads to use",
        type=int,
        default="1",
        )
    parser.add_argument(
        "-v", "--verbose",
        help="Print more debug output",
        action="count",
        default="0",
        )
    args = parser.parse_args()
    main(args)
