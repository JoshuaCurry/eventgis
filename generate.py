from geopandas import GeoSeries
from shapely import Polygon

from model import LayerType


class Point:
    x: float
    y: float

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y


def rectangle(width: float, height: float, anchor: Point) -> Polygon:
    origin = (anchor.x - width / 2, anchor.y - height / 2)
    return Polygon(
        (
            origin,
            (origin[0], origin[1] + height),
            (origin[0] + width, origin[1] + height),
            (origin[0] + width, origin[1]),
            origin,
        )
    )


BAY_LENGTH = 3
BEAM_WIDTH = 0.05

type Structure = dict[LayerType, GeoSeries]


def generate_clearspan(bay_width: int, length: int) -> Structure:
    assert length % BAY_LENGTH == 0

    bays = int(length / BAY_LENGTH) + 1

    origin_x = -(length / 2)

    cover = [rectangle(length, bay_width, Point(0, 0))]
    structure = []

    for bay in range(0, bays):
        x = bay * BAY_LENGTH + origin_x
        structure.append(rectangle(BEAM_WIDTH, bay_width, Point(x, 0)))

    structure.append(rectangle(length, BEAM_WIDTH, Point(0, 0)))

    return {
        LayerType.Cover: GeoSeries(cover),
        LayerType.Structure: GeoSeries(structure),
    }
