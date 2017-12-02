#!/usr/bin/env python

import sys
from datetime import datetime
from elasticsearch import Elasticsearch,TransportError,ConnectionError
from string import ascii_lowercase
from random import randint

es = Elasticsearch(
#  ['host1', 'host2'],
  # sniff from the load balancer to get the direct list of hosts
  # yes, it stops the balancer from balancing, converting it into a
  # really expensive CNAME.  But this is a test. :)
  'http://humpy:8086/elastic',
  # comment out the sniff options to just use the balancer directly
  sniff_on_start=True,
  sniff_on_connection_fail=True
  )

# print out the first connection's information
#c =es.transport.get_connection()
#print c.host
#print c.url_prefix
#sys.exit()

index_name = 'packages'
document_type = 'host'
try:
    es.indices.create(
            index = index_name,
            body  = {
                'settings' : {
                    'index' : {
                        # about 2 billion docs per shard
                        'number_of_shards'   : 3,
                        'number_of_replicas' : 1
                        }
                    },
                "mappings" : {
                    document_type : {
                        "properties" : {
                            "host" :      { 'type' : "text" }, # add a normalizer?
                            "timestamp" : { 'type' : "date" },
                            "packages" :  {
                                'type' : "nested",
                                "properties" : {
                                    "name" :    { 'type' : "text" },
                                    "version" : { 'type' : "text" },
                                    }
                                },
                            }
                        }
                    }
                },
            update_all_types = True,
            )
#except ConnectionError:
except TransportError:
    # catch something other than TransportError to see ElasticSearch errors
    print 'meh'

def gen_host(i):
    hostname = 'host{}'.format(i)
    doc = {
        'host': hostname,
        'timestamp': datetime.now(),
        'packages' : [],
    }
    # add between 5 and 20 single-character "packages"
    for p in ascii_lowercase[:randint(5,20)]:
        doc['packages'].append({
            'name': 'pkg_{}'.format(p),
            'version': '{}.{}.{}'.format(
                randint(1,3),
                randint(0,15),
                randint(0,10),
                )
            })
    res = es.index(index=index_name, doc_type=document_type, id=hostname, body=doc)
    #print res
    #sys.exit()
    print( res['result'], i )

for i in range(1,5000):
    gen_host(i)

## sample fetch and search from the docs
#res = es.get(index="test-index", doc_type='tweet', id=1)
#print(res['_source'])
#
#es.indices.refresh(index="test-index")
#
#res = es.search(index="test-index", body={"query": {"match_all": {}}})
#print res
#print("Got %d Hits:" % res['hits']['total'])
#for hit in res['hits']['hits']:
#    print("%(timestamp)s %(author)s: %(text)s" % hit["_source"])
