# rainbow
Attempt to generate a 3DES rainbow table
 - use threads to separate candidate generation from crypting and storage
 - use queues to communicate between threads
 - use multiprocessing module to distribute processing across systems
 - store in postgresql
