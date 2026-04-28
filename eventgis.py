import click
import geopandas
from geoalchemy2.shape import from_shape
from geopandas import GeoSeries
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from generate import BAY_LENGTH, Structure, generate_clearspan
from model import Base, LayerType, StructureLayer
from model import Structure as DBStructure

DEFAULT_DB = "postgresql://postgres:postgres@localhost/gis"
DEFAULT_INIT_DB = "postgresql://postgres:postgres@localhost/postgres"


@click.group()
@click.option(
    "--database",
    default=DEFAULT_DB,
    envvar="EVENTGIS_DB",
    help="Database connection string.",
)
@click.pass_context
def eventgis(ctx, database):
    """EventGIS Command Line Interface."""
    ctx.obj["engine"] = create_engine(
        make_url(database), connect_args={"connect_timeout": 5}
    )


@eventgis.command()
@click.option(
    "--init-database",
    envvar="EVENTGIS_INIT_DB",
    default=DEFAULT_INIT_DB,
    help="Database connection string.",
)
@click.pass_context
def init(ctx, init_database):
    """Initialise a new EventGIS project."""

    engine = ctx.obj["engine"]
    try:
        engine.connect()
    except OperationalError:
        click.echo(
            f"Cannot connect to database at {ctx.obj['database']}, trying to create DB {url.database}..."
        )
        init_engine = create_engine(init_database, connect_args={"connect_timeout": 5})
        with init_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text(f"CREATE DATABASE {url.database}"))
        click.echo(f"Initialized database at {init_database}")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))

    Base.metadata.create_all(engine)


@eventgis.group()
def structure():
    """Manage structures"""


IMPORT_LAYERS = {"cover": "polygon", "structure": "linestring", "guys": "linestring"}


def insert_structure(
    session: Session, type_name: str, name: str, structure: Structure
) -> None:
    structure_obj = session.scalar(
        select(DBStructure)
        .where(DBStructure.type == type_name)
        .where(DBStructure.name == name)
    )

    if structure_obj is None:
        structure_obj = DBStructure(name=name, type=type_name)
        session.add(structure_obj)

    for layer in structure_obj.layers:
        session.delete(layer)

    for layer_type, series in structure.items():
        if layer_type == LayerType.Cover:
            series = series.polygonize()

        for row in series.force_2d():
            db_layer = StructureLayer(
                structure=structure_obj, layer_type=layer_type, geom=from_shape(row)
            )
            session.add(db_layer)


@structure.command("import")
@click.argument("path", type=click.Path(exists=True))
@click.option("--type", "type_name", required=True)
@click.option("--name", required=True)
@click.pass_context
def import_structure(ctx, path, type_name, name):
    """Load a structure from a DXF file into the database."""
    click.secho(f"Importing {path}...")
    data = geopandas.read_file(path)

    session = Session(ctx.obj["engine"])

    structure = {}
    for layer in data["Layer"].unique():
        if layer.lower() in LayerType:
            items = data[data["Layer"] == layer]
            click.secho(f"Importing {len(items)} objects for layer {layer.lower()}")
            structure[LayerType(layer.lower())] = items

    insert_structure(session, type_name, name, structure)

    session.commit()
    click.secho("Import completed successfully.")


@structure.group()
def generate():
    """Generate structures and insert to the database."""


@generate.command()
@click.option("--width", type=click.IntRange(3, 50), required=True)
@click.option("--min-length", type=click.IntRange(3, 50), required=True)
@click.option("--max-length", type=click.IntRange(3, 50), required=True)
@click.pass_context
def clearspan(ctx, width, min_length, max_length):
    """Generate a set of clearspan structures"""
    session = Session(ctx.obj["engine"])

    type_name = "Clearspan"

    for length in range(min_length, max_length + BAY_LENGTH, BAY_LENGTH):
        name = f"{width}x{length}"
        click.secho(f"Generating {type_name} {name}")
        structure = generate_clearspan(width, length)
        insert_structure(session, type_name, name, structure)

    session.commit()


if __name__ == "__main__":
    eventgis(obj={})
