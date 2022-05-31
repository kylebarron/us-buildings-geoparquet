import json
from pathlib import Path
from typing import Iterable, List, Tuple

import click
import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pygeos


class PathType(click.Path):
    """A Click path argument that returns a pathlib Path, not a string"""

    def convert(self, value, param, ctx):
        return Path(super().convert(value, param, ctx))


def iter_row_groups(parquet_paths: List[Path]) -> Iterable[gpd.GeoDataFrame]:
    for path in parquet_paths:
        parquet_file = pq.ParquetFile(path)
        for i in range(parquet_file.num_row_groups):
            df = parquet_file.read_row_group(i, columns=["geometry"]).to_pandas()
            gdf = gpd.GeoDataFrame(
                {"wkb_geom": df["geometry"]}, geometry=pygeos.from_wkb(df["geometry"])
            )
            yield gdf


def get_num_row_groups(parquet_paths: List[Path]) -> int:
    n = 0
    for path in parquet_paths:
        parquet_file = pq.ParquetFile(path)
        n += parquet_file.num_row_groups

    return n


@click.command()
@click.option(
    "-i",
    "--input",
    type=PathType(readable=True, dir_okay=True, file_okay=False),
    help="Path to input Parquet dataset",
)
@click.option(
    "-o",
    "--output",
    type=PathType(writable=True, dir_okay=True, file_okay=False),
    help="Path to output Parquet dataset",
)
def main(input: Path, output: Path):
    output.mkdir(exist_ok=False, parents=True)

    parquet_paths = list(Path(input).glob("*.parquet"))
    n_row_groups = get_num_row_groups(parquet_paths)

    metadata_collector: List[pq.FileMetaData] = []
    i = 0
    global_id = 0
    with click.progressbar(
        length=n_row_groups, label="Creating global identifier"
    ) as bar:
        for gdf in iter_row_groups(parquet_paths):
            mbr = gdf.geometry.bounds
            index = np.arange(global_id, global_id + len(gdf), dtype=np.uint32)
            global_id += len(gdf)

            to_write = pd.DataFrame(
                {
                    "index": index,
                    "minx": mbr["minx"],
                    "miny": mbr["miny"],
                    "maxx": mbr["maxx"],
                    "maxy": mbr["maxy"],
                    "geometry": gdf["wkb_geom"],
                }
            )

            table = pa.Table.from_pandas(to_write)
            out_path = output / f"part.{i}.parquet"
            with pq.ParquetWriter(
                out_path,
                schema=table.schema,
                version="2.4",
                compression="zstd",
                write_statistics=["minx", "miny", "maxx", "maxy", "index"],
                metadata_collector=metadata_collector,
            ) as writer:
                writer.write_table(table)

            i += 1
            bar.update(1)

    for i, metadata in enumerate(metadata_collector):
        filename = f"part.{i}.parquet"
        metadata.set_file_path(filename)

    full_metadata = metadata_collector[0]
    for _meta in metadata_collector[1:]:
        full_metadata.append_row_groups(_meta)

    full_metadata.write_metadata_file(output / "_metadata")


if __name__ == "__main__":
    main()
