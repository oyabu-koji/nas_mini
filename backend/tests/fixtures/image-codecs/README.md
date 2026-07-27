# Image Codec Test Fixtures

These fixtures are repository-owned, synthetic 8x8 color grids. They contain no user
media and may be used and redistributed with this repository's tests.

The source PPM was generated offline:

```bash
printf '%s' 'UDYKOCA4CjI1NQoAAAD/AAAA/wD//wAAAP//AP8A//////8AAAD/AAD/AP8AAP///wAA/wD/AAD///8AAAAA////AP8AAP///wAA/wD/AAAAAAAAAAAA////AP8AAP///wAA/wD/AAAAAAAAAAAA////AP8AAP///wAA/wD/AAAAAAAAAAAA////AP8AAP///wAA/wD/AAAAAAD/AAD/wAB//wAA/0AA//8AP/+AAP//AL//AAD/wAB//wAA/0AA//8AP/+AAP//AL8=' \
  | base64 --decode > grid.ppm
sips -s format jpeg grid.ppm --out grid.jpg
sips -s format png grid.ppm --out grid.png
sips -s format heic grid.ppm --out grid.heic
```

The source PPM was produced from FFmpeg 8.1.2 `testsrc=size=8x8:rate=1`. The checked-in
files were converted by macOS `sips-316`. `invalid/invalid.heic` is
repository-owned ASCII text created specifically to prove fail-closed decode behavior.
The JPEG and PNG copies under `invalid/` are byte-for-byte copies of the valid fixtures,
so the negative manifest reaches the controlled invalid HEIC input.

SHA-256 values and dimensions are authoritative in each directory's `manifest.json`.
