import enum

from geoalchemy2 import Geometry
from sqlalchemy import ForeignKey, Identity, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Structure(Base):
    __tablename__ = "structure"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(nullable=False)

    layers: Mapped[list["StructureLayer"]] = relationship()


class LayerType(enum.StrEnum):
    Cover = "cover"
    Guys = "guys"
    Structure = "structure"


class StructureLayer(Base):
    __tablename__ = "structure_layer"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)

    structure_id: Mapped[int] = mapped_column(
        ForeignKey("structure.id"), nullable=False
    )
    structure: Mapped[Structure] = relationship(back_populates="layers")

    layer_type: Mapped[LayerType] = mapped_column(String, nullable=False)
    geom: Mapped[Geometry] = mapped_column(Geometry())


Index("structure_layer_structure_id", StructureLayer.structure_id)
