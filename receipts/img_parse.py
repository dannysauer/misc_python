#!/usr/bin/env python3

import pytesseract
from pytesseract import Output
import cv2
import re
from datetime import datetime

#img=cv2.imread('/home/sauer/dev/github/receipt-parser/data/img/IMG_0349.jpeg')
img = cv2.imread('/home/sauer/Pictures/receipts/IMG_0008.jpeg')
img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
#img = cv2.bilateralFilter(img, 9, 75, 75)
img = cv2.GaussianBlur(img, (3,3), 0)
#img = cv2.medianBlur(img, 3)
img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
opening = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel, iterations=1)
img = 255-opening

#d = pytesseract.image_to_data(img, output_type=Output.DICT)
#n_boxes = len(d['level'])
#boxed = img
#for i in range(n_boxes):
#    (x, y, w, h) = (d['left'][i], d['top'][i], d['width'][i], d['height'][i])
#    boxed = cv2.rectangle(boxed, (x, y), (x + w, y + h), (0, 0, 255), 2)

#cv2.imshow('boxed image',boxed)
#cv2.waitKey()
#cv2.imshow('img',thresh)
#cv2.waitKey()
#cv2.imshow('img',opening)
#cv2.waitKey()
#cv2.imshow('img',invert)
#cv2.waitKey()

#text = pytesseract.image_to_string(img, lang='eng')
#text = pytesseract.image_to_string(img)
# PSM options: https://github.com/tesseract-ocr/tesseract/blob/8d6dbb133b41/api/tesseractmain.cpp#L115
text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
#text = pytesseract.image_to_string(invert, lang='eng', config='--psm 12')
#print(text)

date = datetime.now()
nodate = True
# TODO: consider allowing '7' as a delimiter, converting to '/'?
date_re = re.compile(r'(\d{1,4})(?P<delim>[.\-/])(\d{1,2})(?P=delim)(\d{1,4})',
        re.MULTILINE)
for m in date_re.finditer(text):
    d = m.group('delim')  # this is a lot of typing :shrug:
    # ugly, ugly check for zeros detected as 6 - which is somewhat common
    #m1 = m[1]
    #if m1.startswith('6'):
    #    # TODO: probably need to replace m with a new re.match object
    #    print(f"Issue with {m1}")
    #    m1 = str(int(m1) - 60)
    # probably US-format date
    fmt = f'%m{d}%d{d}%y'
    #try:  # not likely at all
        # very clumsily check for year in first position
    if( int(m[1]) > 12 ):
        fmt = f'%y{d}%m{d}%d'
    #except ValueError as e:
    #    print(f"Unable to parse '{m[1]}' as integer: {e}")
    #    continue
    try:
        parsed_date = datetime.strptime(m[0], fmt)
    except ValueError as e:
        #print(f"Unable to parse '{m[0]}': {e}")
        continue
    nodate = False
    #print(f"'{m[0]}' parsed as {parsed_date}")
    if parsed_date < date:
        date = parsed_date

if nodate:
    print("Didn't find a date")
else:
    print(f"Decided on: {date}")

total = 0
total_re = re.compile(r'total\b\D*(\d+(.\d{2}))?',
        re.MULTILINE | re.IGNORECASE | re.ASCII)
for m in total_re.finditer(text):
    # no try/except, since regex only accepts digits
    val = float(m[1])
    if val > total:
        total = val
if total:
    print(f"total: {total}")
else:
    print("No total found")
