import time
import logging
import struct
import sys
import io
import multiprocessing.queues
import redis
from typing import Union, Tuple, IO
from PIL import Image

class Camera (object ) :
    def __init__(self, device_uri: str, sleep_time: int, debug: bool = False):
        super().__init__()
        self._device_uri = device_uri
        self._sleep_time = sleep_time
        self._debug = debug
        self._width = -1
        self._height = -1
        self._output = None

    def _poll_once(self) -> None:
        raise NotImplementedError ('Must implement abstract method')

    def _write_data(self, data: bytearray):
        if isinstance(self._output, queue.Queue):
            self._output.put(data)
        else:
            self._output.write(data)

    def poll_image(self, output: Union[IO, multiprocessing.queues.Queue]) -> None:
        self._output = output

        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                sys.exit(0)
            except BrokenPipeError:
                sys.exit(0)
            except Exception:
                logging.exception("")
            finally:
                pass

    @property
    def size(self) -> Tuple[int, int]:
        return (self._width, self._height)

    def get_jpeg(self, data, size=(0, 0)) -> bytearray:
        jpeg_data = io.BytesIO()
        image = Image.frombytes("RGB", self.size, data, "raw")

        if size[0]:
            image = image.resize(size)

        image.save(jpeg_data, format="JPEG")
        jpeg_data = jpeg_data.getvalue()

        return jpeg_data


class RedisStreamer(Camera):
    def __init__(self, device_uri: str = "localhost:6379",
                 sleep_time: int = 1,
                 debug: bool = False):

        super().__init__( device_uri, sleep_time, debug)

        self.redis = self._connect()
        self._last_frame_number = -1



    def _connect(self):
        return redis.Redis()

    def _get_image(self) -> Tuple[bytearray, float, float, int]:
        frame_data = self.redis.get("last_image_data")
        frame_no = int(self.redis.get("last_image_id"))
        if not (frame_data and frame_no):
            print ("Oh NO! Data was impossible to get !")

        width, height = 1024 , 1360

        return frame_data, width, height, frame_no


    def _poll_once(self) -> None:
        frame_no = int(self.redis.get("last_image_id"))
        print(f"Tis is frame number  ----> {frame_no}")

        if self._last_frame_number != frame_no:
            raw_data, width, height, frame_number = self._get_image()
            self._raw_data = raw_data

            self._write_data(self._raw_data)
            self._last_frame_number = frame_number



if __name__ == "__main__":
    import queue
    import threading

    device_uri = "localhost:6379"
    sleep_time = 2
    debug = True

    camera = RedisStreamer(device_uri=device_uri, sleep_time=sleep_time, debug=debug)

    output_queue = queue.Queue()

    def handle_output(output_queue):
        while True:
            data = output_queue.get()
            if data is None:
                break
            print(f"Received data: {len(data)} bytes")

    output_thread = threading.Thread(target=handle_output, args=(output_queue,))
    output_thread.start()

    try:
        camera.poll_image(output_queue)
    except Exception as e:
        print(f"Error during polling: {e}")
    finally:
        output_queue.put(None)
        output_thread.join()

    print("Doie")