#!/usr/bin/env python3
#
#   Hello World client in Python
#   Connects REQ socket to tcp://localhost:5555
#   Sends "Hello" to server, expects "World" back
#

import zmq
import timeit
#import threading

def ping_z(z, count=10):
    #for req in range (count):
        z.send(b"Hello")
        message = z.recv()
    #    print( "Received '{}' (#{})".format( message, req ), end="\r" )


if __name__ == "__main__":
    count = 100000
    context = zmq.Context()

    #  Create socket to talk to server
    print("Connecting to hello world server…")
    socket = context.socket(zmq.REQ)
    socket.connect("tcp://localhost:5555")

    print("Sending {} messages".format(count))
    print(
            timeit.timeit(
                "ping_z(socket)",
                setup="from __main__ import ping_z, socket",
                number=count
                )
            )

    #t=threading.Thread( target=ping_z, args=(socket, 10) )
#
#    t.start()
#    t.join()
