#!/usr/bin/env python3

import cv2
import numpy as np
import os
#import fcntl
from sys import exit

cascPath = "opencv/data/haarcascades/haarcascade_frontalface_default.xml"
faceCascade = cv2.CascadeClassifier(cascPath)

cap = cv2.VideoCapture('/dev/video0', cv2.CAP_V4L2)

#height, width = 720, 1280  # 720p
height, width = 480, 858   # 480p
#height, width = 480, 640    # vga
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
cap.set(cv2.CAP_PROP_FPS, 60)

success, frame = cap.read()

if not success:
  exit(1) 

gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
faces = faceCascade.detectMultiScale(
    gray,
    scaleFactor=1.1,
    minNeighbors=5,
    minSize=(30, 30),
)
for (x, y, w, h) in faces:
    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
cv2.imwrite("test2.jpg", frame)

exit(0)
