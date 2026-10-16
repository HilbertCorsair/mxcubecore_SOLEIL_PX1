"""Ask the running murko server what it returns for the crystal.

Read-only: sends one image, prints the description keys and the crystal mask
summary. Run on proxima1 in the mxcubeweb env, with MURKO_HOST/MURKO_PORT set
as for MXCuBE, from the mxcubecore checkout:

    python scripts/murko/check_murko_crystal.py            # current redis camera frame
    python scripts/murko/check_murko_crystal.py image.jpg  # a saved frame

The grid colouring needs descriptions[0]["crystal"]["notion_mask"]; if it is
missing, the second request (raw_predictions) shows whether the probability
maps can be used instead.
"""

import os
import pickle
import sys

import numpy as np
import zmq


def ask(image, **extra):
    request = {
        "to_predict": image,
        "description": [
            "foreground",
            "crystal",
            "loop_inside",
            "loop",
            ["crystal", "loop"],
            ["crystal", "loop", "stem"],
        ],
        "save": False,
        "prefix": "predicted",
        **extra,
    }
    context = zmq.Context()
    sock = context.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        sock.connect("tcp://%s:%s" % (os.environ["MURKO_HOST"], os.environ["MURKO_PORT"]))
        sock.send(pickle.dumps(request))
        if not sock.poll(30000):
            sys.exit("murko did not answer in 30 s")
        return pickle.loads(sock.recv())
    finally:
        sock.close()
        context.term()


def summary(value):
    if isinstance(value, np.ndarray):
        return "array %s %s, %d non-zero" % (value.shape, value.dtype, np.count_nonzero(value))
    return repr(value)[:120]


if len(sys.argv) > 1:
    with open(sys.argv[1], "rb") as fd:
        image = fd.read()  # JPEG bytes, as murko accepts them
    print("image: %s" % sys.argv[1])
else:
    from redis_camera import camera

    image = camera().get_image()  # what PX1MiniDiff.takePictureAnalysis sends
    print("image: redis camera frame %s" % (getattr(image, "shape", None),))

analysis = ask(image)
print("reply keys:", sorted(analysis))
print("original_image_shape:", analysis.get("original_image_shape"))
description = analysis["descriptions"][0]
print("description keys:", sorted(description))
for key in ("present", "original_shape", "prediction_shape", "aoi_bbox", "crystal_bbox",
            "most_likely_click"):
    print("  %-18s %s" % (key, summary(description.get(key, "<missing>"))))
crystal = description.get("crystal")
if isinstance(crystal, dict):
    print("crystal keys:", sorted(crystal))
    for key, value in sorted(crystal.items()):
        print("  crystal.%-10s %s" % (key, summary(value)))
else:
    print("crystal:", summary(crystal))

if not (isinstance(crystal, dict) and "notion_mask" in crystal):
    raw = ask(image, raw_predictions=True).get("predictions")
    print("raw_predictions:", type(raw).__name__,
          [summary(p) for p in raw] if isinstance(raw, (list, tuple)) else summary(raw))
