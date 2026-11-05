import cv2
import time

print("Testing both webcams:")
for idx in [0, 2]:
    print(f"--- Opening /dev/video{idx} ---")
    cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"FAILED to open /dev/video{idx}")
        continue
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(5):
        ret, frame = cap.read()
        time.sleep(0.05)
    if ret and frame is not None:
        print(f"SUCCESS: /dev/video{idx} captured frame of shape {frame.shape}")
    else:
        print(f"FAILED to read frame from /dev/video{idx}")
    cap.release()
print("Test completed.")
