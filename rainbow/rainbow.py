#!/usr/bin/python
import crypt
import psycopg2
from string import letters,digits,printable
from itertools import product
from Queue import Queue
from threading import Thread

salts = []
#saltchars = 'ab'
#saltchars = digits
saltchars = letters + digits + './'
for salt in product(saltchars, repeat=2):
    salts.append( ''.join(salt) )

def rainbow(password):
    rs = []
    for salt in salts:
        #rs.append( {'p':password, 'h':salt + password} )
        rs.append( {
            'p':password,
            'h':crypt.crypt(password, salt),
            } )
    return rs

def generate_data(q, maxlen=2, minlen=1):
    """ create base passwords for consumer threads to crypt
    """
    alphabet = 'ab'
    #alphabet = printable
    for l in range(minlen, maxlen+1):
        for s in product(alphabet, repeat=l):
            q.put( ''.join(s) )

def consume_data(in_q, out_q):
    """ pull data off of the queue to encrypt
    """
    while True:
        out_q.put( rainbow( in_q.get() ))
        in_q.task_done()

def record_data(q):
    """ pull data from the queue and add to database
    """
    db = psycopg2.connect(
        dbname='rainbow',
        host='humpy',
        user='rainbow',
        password='bowrain',
        );
    cur = db.cursor();
    while True:
        vals = q.get()
        for val in vals:
            #print val['h']
            try:
                cur.execute("""
                    INSERT INTO three_des
                    (pass, hash)
                    VALUES(%(p)s, %(h)s)
                    """,
                    val
                    )
            except:
                print "Failed to insert"
        db.commit()
        q.task_done()

passwords = Queue(maxsize=0)
hashes    = Queue(maxsize=0)

# only one generator
for i in range(1):
    t = Thread( target=generate_data, args=(passwords, 2, 1))
    t.daemon = True
    t.start()

# however many consumers
for i in range(3):
    t = Thread( target=consume_data, args=(passwords, hashes))
    t.daemon = True
    t.start()

# only one data recorder?
for i in range(2):
    t = Thread( target=record_data, args=(hashes,))
    t.daemon = True
    t.start()

passwords.join()
hashes.join()
