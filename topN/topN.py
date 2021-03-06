#!/usr/bin/env python

import fileinput

class dub_link_list_elem:
    def __init__(self, val=None, next_node=None, prev_node=None):
        self.val = val
        self.next_node = next_node
        self.prev_node = prev_node

    def get_val(self):
        return self.val

    def get_next(self):
        return self.next_node

    def set_next(self, new_node=None):
        self.next_node = new_node

    def get_prev(self):
        return self.prev_node

    def set_prev(self, new_node=None):
        self.prev_node = new_node

class dub_link_list:
    def __init__(self, head=None, max_len=1):
        self.head = head
        self.tail = head
        self.len = 0
        self.max_len = max_len
        self.min = None

    def check_bigger(self, val):
        if val > self.min or self.len < self.max_len:
            self.insert(val)

    def insert(self, val):
        new_node = dub_link_list_elem(val)
        if self.head == None:
            self.min  = val
            self.head = new_node
            self.tail = new_node
            self.len += 1
            return

        prev = None
        for node in self.nodes():
            if node == None:
                # we are the new tail
                prev.next_node = new_node
                new_node.prev_node = prev
                self.tail = new_node
                self.len += 1
                break # somewhat duplicated
            if val == node.val:
                # already in the list; skip
                return
            if val > node.val:
                # add node
                new_node.next_node = node
                new_node.prev_node = prev
                node.prev_node = new_node
                # deal with previous node
                if prev != None:
                    prev.next_node = new_node
                else:
                    # updated the head
                    self.head = new_node
                self.len += 1
                break
            prev = node

        # the whole reason for double-link is to ease insertion while
        #  keeping the list size constant (or at least <= max_len)
        while self.len > self.max_len:
            self.tail = self.tail.prev_node
            self.tail.next_node = None
            self.len -= 1

        self.min = self.tail.val

    def nodes(self):
        cur = self.head
        while cur != None:
            yield cur
            #cur = cur.get_next()
            cur = cur.next_node
        yield None

    def vals(self):
        cur = self.head
        while cur != None:
            yield cur.get_val()
            cur = cur.get_next()

# I want to read one line from fileinput, but I don't want to use the
#  "isFirstLine" check on every single line in the loop
lines= iter(fileinput.input())
line = next(lines)
numlist = dub_link_list(max_len=int(line.strip()))

for line in lines:
    numlist.check_bigger(int(line.strip()))

for v in numlist.vals():
    print v
