# Murko tools — PROXIMA-1

The unattended collect asks the murko server (loop and crystal segmentation,
https://github.com/MartinSavko/murko) where the loop and the crystal are
(`PX1MiniDiff.estimate_click_murko`). The grid overlay colours the grid cells
green on the crystal and red off it from the crystal mask murko sends,
`descriptions[0]["crystal"]["notion_mask"]`. An older murko server may not
send that mask: then every cell stays green and no outline is drawn.

## check_murko_crystal.py

Sends one image to the running murko server and prints what comes back: the
description keys, `aoi_bbox`, `crystal_bbox`, and every key of the crystal
description, with array shapes and the number of non-zero pixels. It changes
nothing.

```sh
# mxcubeweb env, MURKO_HOST / MURKO_PORT set as for MXCuBE
python scripts/murko/check_murko_crystal.py            # current redis camera frame
python scripts/murko/check_murko_crystal.py image.jpg  # a saved frame
```

The overlay works when the output lists `crystal keys: [... 'notion_mask' ...]`
and, with a crystal in view, the mask has non-zero pixels. If `notion_mask` is
missing, the script also asks for `raw_predictions` and prints what the
probability maps look like, to decide between updating the murko server and
thresholding those maps.
