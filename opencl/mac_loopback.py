#!/usr/bin/env python3

import cv2
import numpy as np
import os
#import fcntl
from sys import exit

cap = cv2.VideoCapture('/dev/video0', cv2.CAP_V4L2)

#height, width = 720, 1280  # 720p
#height, width = 480, 858   # 480p
height, width = 480, 640    # vga
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
cap.set(cv2.CAP_PROP_FPS, 60)

success, frame = cap.read()

if not success:
  exit(1) 

cv2.imwrite("test.jpg", frame)

exit(0)
