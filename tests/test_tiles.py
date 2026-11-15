import numpy as np

from jedai.perception.tiles import save_pieces, tile_boxes


def test_tiles_cover_frame_with_overlap():
    boxes = tile_boxes(2560, 1440, 3, 3, 0.1)
    assert len(boxes) == 9
    cover = np.zeros((1440, 2560), bool)
    for _, x0, y0, x1, y1 in boxes:
        cover[y0:y1, x0:x1] = True
        assert x1 - x0 <= 2560 / 3 * 1.2 + 1
    assert cover.all()
    assert boxes[1][1] < 2560 / 3                     # r0c1 starts left of the 1/3 line


def test_save_pieces(tmp_path):
    img = np.random.randint(0, 255, (1440, 2560, 3), np.uint8)
    paths = save_pieces(img, tmp_path, regions={"chat": (0.2, 0.8, 0.7, 1.0)})
    assert len(paths) == 10 and (tmp_path / "chat.jpg").exists()
    assert "chat.jpg  x 512-1792  y 1152-1440" in (tmp_path / "index.txt").read_text()
