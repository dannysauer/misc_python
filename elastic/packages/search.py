#!/usr/bin/env python

import json
from elasticsearch import Elasticsearch
from string    import ascii_lowercase
from random    import randint

es = Elasticsearch(
#  ['host1', 'host2'],
  # sniff from the load balancer to get the direct list of hosts
  # yes, it stops the balancer from balancing, converting it into a
  # really expensive CNAME.  But this is a test. :)
  'http://humpy:8086/elastic',
  # comment out the sniff options to just use the balancer directly
  # note that this uses a persistent connection, so it won't work well :)
  sniff_on_start=True,
  sniff_on_connection_fail=True
  )

# print out the first connection's information
#c =es.transport.get_connection()
#print c.host
#print c.url_prefix
#sys.exit()

## sample fetch and search from the docs
#res = es.get(index="packages", doc_type='host', id='host437')
#print( json.dumps(res['_source'], indent=2) )

#es.indices.refresh(index="packages")
#
res = es.search(
        index="packages", 
        body={"query": {
            "nested" : {
                'path'  : 'packages',
                'query' : {
                    'bool': {
                        'must' : {
                            'match' : {
                                'packages.name' : 'pkg_q'
                                }
                            }
                        }
                    }
                }
            }}
        )
res = es.search(
        index="packages", 
        body={
            'query' : { 'nested' : {
                'path'  : 'packages',
                'query' : {
                    'bool': {
                        'must' : {
                            'match' : {
                                'packages.name' : 'pkg_t'
                                }
                            }
                        }
                    }
                }
            }}
        )
#print dir(res)
#for hit in res.keys:
#    print hit
print("Got %d Hits:" % res['hits']['total'])
for hit in res['hits']['hits']:
    for h in hit:
        print h
    #print dir(hit)
    #print hit['host']
#    print("%(timestamp)s %(author)s: %(text)s" % hit["_source"])
