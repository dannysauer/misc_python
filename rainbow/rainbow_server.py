#!/usr/bin/python
import crypt
import psycopg2
import multiprocessing
from string import letters, digits, printable
from itertools import product
# from threading import Thread


def generate_data(q, maxlen=2, minlen=1):
    """ create base passwords for consumer threads to crypt
    """
    alphabet = 'ab'
    alphabet = printable
    for l in range(minlen, maxlen+1):
        for s in product(alphabet, repeat=l):
            q.put(''.join(s))


def record_data(q):
    """ pull data from the queue and add to database
    """
    salt_chars = letters + digits + './'
    db = psycopg2.connect(
        dbname='rainbow',
        host='humpy',
        user='rainbow',
        password='bowrain',
        )
    cur = db.cursor()
    while True:
        vals = q.get()
        for val in vals:
            # print val['h']
            for salt in product(salt_chars, 2):
                try:
                    print("{v}: {h}}".format(v=val, h=crypt(val, salt)))
                    cur.execute(
                        """
                        INSERT INTO three_des
                        (pass, hash)
                        VALUES(%(p)s, %(h)s)
                        """,
                        val,
                        crypt(val, salt),
                    )
                except:
                    print("Failed to insert")
        db.commit()
        q.task_done()


passwords = multiprocessing.JoinableQueue(maxsize=0)
hashes    = multiprocessing.JoinableQueue(maxsize=0)

# only one generator
for i in range(1):
    t = multiprocessing.Process(target=generate_data, args=(passwords, 5, 1))
    t.start()

# only one data recorder?
for i in range(2):
    t = multiprocessing.Process(target=record_data, args=(hashes,))
    t.start()

passwords.join()
print("all passwords consumed")
hashes.join()
print("all hashes done")
for p in multiprocessing.active_children():
    print("joining process ", p.pid)
    if not p.join(2):
        print("terminating process ", p.pid)
        p.terminate()
