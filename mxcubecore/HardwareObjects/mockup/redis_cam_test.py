import cv2
import numpy as np
import redis

r = redis.Redis(host = "localhost", port = 6379, db = 0)


stream_url = "http//localhost:6379"
cap = cv2.VideoCapture(stream_url)

ret, frame = cap.read()

if ret:
    cv2.imshow('Test frame', frame)
    cv2.waitKey(0)
else:
    print("FAIL !")

cap.release()
cv2.destroyAllWindows()