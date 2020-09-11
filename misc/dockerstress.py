#!/usr/bin/env python3

import argparse
import docker
import requests
import threading
from time import monotonic_ns

amillion = 1000000000

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


class TimerNS:
    """ Creates an object which tracks nanoseconds since creation
    """
    def __init__(self):
        self.initial_time = monotonic_ns()
        self.last_time = self.initial_time

    def __get__(self):
        self.last_time = monotonic_ns()
        return self.last_time - self.initial_time

    def __truediv__(self, other): return self.__get__() / other
    def __floordiv__(self, other): return self.__get__() // other
    def __sub__(self, other): return self.__get__() - other
    def __rsub__(self, other): return other - self.__get__()
    def __str__(self): return self.__get__().__str__()

    def since_begin(self):
        """ Return nanoseconds since the object was created
        """
        return self.__get__()

    def since_last(self):
        """ Return nanoseconds since most recent get/reset/last
        """
        now = monotonic_ns()
        last = self.last_time
        self.last_time = now
        return now - last

    def reset(self):
        self.initial_time = monotonic_ns()
        return 0


class Dockerstress:
    def __init__(self, target_instances=1, threads=1):
        self.target_instances = target_instances
        self.thread_count = threads

        self.client = docker.from_env()
        self.containers = []
        self.index = Atomic_counter()
        self.timer = TimerNS()

    def runall(self):
        self.timer.reset()
        self.create_containers()
        self.verify_containers()
        self.cleanup()
        endtimes = self.timer.since_begin()
        print(f"[{endtimes}]\tEnd (took {endtimes//amillion} seconds total)")

    def create_containers(self):
        print(f"[{self.timer.reset()}]\tBegin create")
        for i in range(1, self.target_instances + 1):
            self._create_container()
        print(f"[{self.timer}]\tCreate took {self.timer//amillion} seconds")

    def _create_container(self):
        i = self.index.increment()
        host = f"stresshost_{i}"
        print(f"[+{self.timer.since_last()}]\tcreating {host}")
        cont = self.client.containers.run(
            "busybox",
            ["/bin/httpd", "-fh", "/etc"],
            hostname=host,
            name=host,
            remove=True,
            detach=True,
            ports={"80/tcp": None},  # picks a random port
            publish_all_ports=True,
            )
        # have to get the container after it's started so things like
        # "assigned ports" are fully populated.  Otherwise we just get
        # the configuration value, which is None :/
        self.containers.append(
            self.client.containers.get(cont.name)
            )

    def verify_containers(self):
        stime = self.timer.since_begin()
        print(f"[{stime}]\tBegin verify")
        for cont in self.containers:
            self._verify_container(cont)
        print(f"[{self.timer}]\tVerify took {(self.timer-stime)//amillion} seconds")

    def _verify_container(self, cont):
        print(f"[+{self.timer.since_last()}]\tVerifying {cont.name}")
        eport = cont.attrs['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
        # print(f"Container {cont.name} exposes port {eport}")
        url = f"http://localhost:{eport}/hostname"
        r = requests.get(url)
        print(f"{url} -> '{r.text.rstrip()}'")

    def cleanup(self):
        stime = self.timer.since_begin()
        print(f"[{stime}]\tBegin cleanup")
        for cont in self.containers:
            print(f"[+{self.timer.since_last()}]\tCleaning up {cont.name}")
            cont.kill()
            # cont.remove()
        print(f"[{self.timer}]\tCleanup took {(self.timer-stime)//amillion} seconds")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "number",
        help="number of instances (defaults to 1)",
        type=int,
        default=1,
        nargs='?',  # make this optional
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

    d = Dockerstress(target_instances=args.number, threads=args.threads)
    d.runall()
