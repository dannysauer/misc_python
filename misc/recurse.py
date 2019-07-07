#!/usr/bin/env python3

import webbrowser
import httplib2
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup, SoupStrainer

site = None
browser = None
http = None
known_links = set()

def local_link(tag):
  global site
  return tag.has_attr('href') and \
         urlparse(tag['href']).netloc == site

def get_links(url):
  global known_links
  global http
  known_links.add(url)
  status, response = http.request(url)
  soup = BeautifulSoup(response, 'html.parser')
  links = set( urljoin( url, x['href'] ) for x in soup.find_all(local_link))
  print("found {} dup links".format( len(links.intersection(known_links)) ))
  return( links.difference( known_links ) )

def recurse(url):
  global browser
  global known_links
  browser.open_new_tab(url)
  print(f"recursing over {url}")
  new_links = get_links(url)
  print( "Found {} new links on {}".format(len(new_links), url) )
  known_links.update(new_links)
  for link in new_links:
    recurse(link)

if __name__ == '__main__':
  url = 'http://www.google.com/'
  site = urlparse(url).netloc
  http = httplib2.Http()
  #proxy = httplib2.proxy_info_from_url('http://squid:3128')
  #http = httplib2.Http(proxy_info=proxy)
  browser = webbrowser.get();
  recurse(url)
