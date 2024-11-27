from flask import Flask, Response
import cv2
import numpy as np
from RedisCamera_original import camera as RC
import time

class FlaskVideoApp(RC):
    def __init__(self, name):
        super().__init__()

    def get_video_stream(self):
        while True:
            frame = self.get_rgbimage()  # Fetch the latest frame
            if frame is not None:
                ok, buffer = cv2.imencode('.jpg', frame)
                if ok :
                    frame_bytes = buffer.tobytes()
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                else:
                    print("NOT OK! Check cv2.imcode in get_video_stream()")
            else:
                print("NO frame !")
                # If no frame is available, you might want to sleep for a bit
                time.sleep(0.1)

app = Flask(__name__)
cam = FlaskVideoApp("RedisToFlask")

@app.route('/video_feed')
def video_feed():
    return Response(cam.get_video_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/set_exposure', methods=['POST'])
def set_exposure():
    """Route to set camera exposure"""
    exposure_value = request.json.get('exposure')
    camera.set_exposure(exposure_value)
    return jsonify({"success": True})

@app.route('/set_gain', methods=['POST'])
def set_gain():
    """Route to set camera gain"""
    gain_value = request.json.get('gain')
    camera.set_gain(gain_value)
    return jsonify({"success": True})

"""
if __name__ == '__main__':
    app.run(debug=True, threaded=True)
"""

if __name__ == '__main__':
    app.run(host = '127.0.0.1', port = 8081, debug=True, threaded=True)