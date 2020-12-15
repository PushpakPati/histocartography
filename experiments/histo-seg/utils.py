"""This module handles helper general functions"""
import argparse
import gc
import importlib
import logging
import random
import re
from pathlib import Path
from typing import Any, Optional, Tuple

import cv2
import dgl
import matplotlib as mpl
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import seaborn as sns
import torch
import yaml
from PIL import Image
from skimage.segmentation import mark_boundaries


def fast_mode(input_array: np.ndarray, nr_values: int, axis: int = 0) -> np.ndarray:
    """Calculates the mode of an tensor over an axis where only values from 0 up to (excluding) nr_values occur.

    Args:
        x (np.array): Input Tensor
        nr_valuesint (int): Possible values. From 0 up to (exclusing) nr_values.
        axis (int, optional): Axis to do the mode over. Defaults to 0.

    Returns:
        np.array: Output Tensor
    """
    output_array = np.empty((nr_values, input_array.shape[1], input_array.shape[2]))
    for i in range(nr_values):
        output_array[i, ...] = (input_array == i).sum(axis=axis)
    return np.argmax(output_array, axis=0)


def read_image(image_path: str) -> np.ndarray:
    """Reads an image from a path and converts it into a numpy array

    Args:
        image_path (str): Path to the image

    Returns:
        np.array: A numpy array representation of the image
    """
    assert image_path.exists()
    try:
        with Image.open(image_path) as img:
            image = np.array(img)
    except OSError as e:
        logging.critical(f"Could not open {image_path}")
        raise OSError(e)
    return image


def start_logging(level="INFO") -> None:
    """Start logging with the standard format

    Args:
        level (str, optional): Logging level. Defaults to "INFO".
    """
    logging.basicConfig(
        level=level,
        format="%(levelname)s - %(module)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.info("Start logging")


def show_superpixel_heatmap(superpixels: np.ndarray) -> None:
    """Show a heatmap of the provided superpixels

    Args:
        superpixels (np.array): Superpixels
    """
    fig, ax = plt.subplots(figsize=(14, 14))
    sns.heatmap(superpixels - 1, annot=True, fmt="d", ax=ax, square=True, cbar=False)
    ax.set_axis_off()
    fig.show()


def show_graph(graph: dgl.DGLGraph) -> None:
    """Show DGL graph with Kamanda Kawai Layout

    Args:
        graph (dgl.DGLGraph): Graph to show
    """
    nx_G = graph.to_networkx().to_undirected()
    # Kamada-Kawaii layout usually looks pretty for arbitrary graphs
    pos = nx.kamada_kawai_layout(nx_G)
    nx.draw(nx_G, pos, with_labels=True, node_color=[[0.7, 0.7, 0.7]])


def get_next_version_number(path: Path) -> int:
    """Get next ascending version number

    Args:
        path (Path): Path to check for prior versions

    Returns:
        int: Next version number
    """
    if not path.exists():
        path.mkdir()
    existing = list(
        map(lambda x: int(re.findall(r"[0-9]+", x.name)[0]), path.iterdir())
    )
    if len(existing) == 0:
        return 0
    else:
        return max(existing) + 1


def dynamic_import_from(source_file: str, class_name: str) -> Any:
    """Do a from source_file import class_name dynamically

    Args:
        source_file (str): Where to import from
        class_name (str): What to import

    Returns:
        Any: The class to be imported
    """
    module = importlib.import_module(source_file)
    return getattr(module, class_name)


def merge_metadata(
    image_metadata: pd.DataFrame,
    annotation_metadata: pd.DataFrame,
    graph_directory: Optional[Path] = None,
    superpixel_directory: Optional[Path] = None,
    processed_image_directory: Optional[Path] = None,
    add_image_sizes: bool = False,
):
    # Prepare annotation paths
    annot = annotation_metadata[annotation_metadata.pathologist == 1][
        ["path", "name"]
    ].set_index("name")
    annot = annot.rename(columns={"path": "annotation_path"})

    # Join with image metadata
    image_metadata = image_metadata.join(annot)

    if graph_directory is not None:
        graph_metadata = pd.DataFrame(
            [(path.name.split(".")[0], path) for path in graph_directory.iterdir()],
            columns=["name", "graph_path"],
        )
        graph_metadata = graph_metadata.set_index("name")
        image_metadata = image_metadata.join(graph_metadata)

    if superpixel_directory is not None:
        superpixel_metadata = pd.DataFrame(
            [
                (path.name.split(".")[0], path)
                for path in superpixel_directory.iterdir()
            ],
            columns=["name", "superpixel_path"],
        )
        superpixel_metadata = superpixel_metadata.set_index("name")
        image_metadata = image_metadata.join(superpixel_metadata)

    if processed_image_directory is not None:
        preprocessed_metadata = pd.DataFrame(
            [
                (path.name.split(".")[0], path)
                for path in processed_image_directory.iterdir()
            ],
            columns=["name", "processed_image_path"],
        )
        preprocessed_metadata = preprocessed_metadata.set_index("name")
        image_metadata = image_metadata.join(preprocessed_metadata)

    # Add image sizes
    if add_image_sizes:
        image_heights, image_widths = list(), list()
        for name, row in image_metadata.iterrows():
            image = Image.open(row.annotation_path)
            height, width = image.size
            image_heights.append(height)
            image_widths.append(width)
        image_metadata["height"] = image_heights
        image_metadata["width"] = image_widths

    return image_metadata


def compute_graph_overlay(
    graph: dgl.DGLGraph,
    path: Optional[str] = None,
    image: Optional[np.ndarray] = None,
    superpixels: Optional[np.ndarray] = None,
    scale_factor: float = 1.0,
) -> Optional[Tuple[mpl.figure.Figure, mpl.axes.Axes]]:
    """Creates a plot of the graph, optionally with labels, overlayed with the image or even with the image and superpixels. Saves to name if not None.

    Args:
        graph (dgl.DGLGraph): Graph to plot
        path (Optional[str], optional): Path to save to. None refers to returning instead of saving. Defaults to None.
        image (Optional[np.ndarray], optional): Image that goes with the graph. Defaults to None.
        superpixels (Optional[np.ndarray], optional): Superpixels that go with the image. Defaults to None.

    Returns:
        Optional[Tuple[plt.figure.Figure, plt.axes.Axis]]: Either None if name is not None or returns the fig, ax like plt.subplots
    """

    fig, ax = plt.subplots(figsize=(30, 30))
    if image is not None:
        new_dim = (
            int(image.shape[0] * scale_factor),
            int(image.shape[0] * scale_factor),
        )
        image = cv2.resize(image, new_dim, interpolation=cv2.INTER_NEAREST)
        if superpixels is not None:
            superpixels = cv2.resize(
                superpixels, new_dim, interpolation=cv2.INTER_NEAREST
            )
            image = mark_boundaries(image, superpixels, color=(0, 1, 1))
        ax.imshow(image, alpha=0.5)

    nxgraph = graph.to_networkx()

    if "label" in graph.ndata:
        color_map = []
        for i in range(nxgraph.number_of_nodes()):
            label = graph.ndata["label"][i].item()
            if label == 4:
                color_map.append("gray")
            if label == 0:
                color_map.append("lime")
            if label == 1:
                color_map.append("mediumblue")
            if label == 2:
                color_map.append("gold")
            if label == 3:
                color_map.append("darkred")
    else:
        color_map = "black"
    pos = (graph.ndata["centroid"].numpy().copy()) * scale_factor
    pos[:, [0, 1]] = pos[:, [1, 0]]
    nx.draw_networkx(
        nxgraph,
        pos=pos,
        node_color=color_map,
        arrows=False,
        with_labels=False,
        ax=ax,
        node_size=40 * scale_factor,
        width=0.5 * scale_factor,
        edge_color="black",
    )
    if path is None:
        return fig, ax
    fig.savefig(path, dpi=100, bbox_inches="tight")

    # Clear the current axes.
    plt.cla()
    # Clear the current figure.
    plt.clf()
    # Closes all the figure windows.
    plt.close("all")
    plt.close(fig)
    gc.collect()


def fix_seeds(seed: Optional[int] = None) -> int:
    """Fixes all the seeds to a random value or a provided seed and returns the seed

    Args:
        seed (Optional[int], optional): Seed to use. Defaults to None.

    Returns:
        int: The used seed
    """
    if seed is None:
        seed = random.randint(0, 2 ** 31 - 1)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    dgl.random.seed(seed)
    return seed


def get_config(name="train", default="default.yml", required=[]):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default=default)
    parser.add_argument("--level", type=str, default="WARNING")
    parser.add_argument("--test", action="store_const", const=True, default=False)
    args = parser.parse_args()

    start_logging(args.level)
    assert Path(args.config).exists(), f"Config path does not exist: {args.config}"
    with open(args.config) as config_file:
        config = yaml.load(config_file, Loader=yaml.FullLoader)
    assert name in config, f"Config does not have an entry {name} ({config.keys()})"
    config = config[name]

    for key in required:
        assert (
            key in config
        ), f"{key} not defined in config {args.config}: {config.keys()}"

    return config, args.config, args.test


class SuperpixelVisualizer:
    """Helper class that handles visualizing superpixels in a notebook"""

    def __init__(
        self, height: int = 14, width: int = 14, patch_size: int = 1000
    ) -> None:
        """Helper class to display the output of superpixel algorithms

        Args:
            height (int, optional): Height of the figure. Defaults to 14.
            width (int, optional): Width of the figure. Defaults to 14.
            patch_size (int, optional): Size of a random patch. Defaults to 1000.
        """
        self.height = height
        self.width = width
        self.patch_size = patch_size

    def show_random_patch(self, image: np.ndarray, superpixels: np.ndarray) -> None:
        """Show a random patch of the given superpixels

        Args:
            image (np.array): Input image
            superpixels (np.array): Input superpixels
        """
        width, height, _ = image.shape
        patch_size = min(width, height, self.patch_size)
        x_lower = np.random.randint(0, width - patch_size)
        x_upper = x_lower + patch_size
        y_lower = np.random.randint(0, height - patch_size)
        y_upper = y_lower + patch_size
        self.show(
            image[x_lower:x_upper, y_lower:y_upper],
            superpixels[x_lower:x_upper, y_lower:y_upper],
        )

    def show(self, image: np.ndarray, superpixels: np.ndarray) -> None:
        """Show the given superpixels overlayed over the image

        Args:
            image (np.array): Input image
            superpixels (np.array): Input superpixels
        """
        fig, ax = plt.subplots(figsize=(self.height, self.width))
        marked_image = mark_boundaries(image, superpixels)
        ax.imshow(marked_image)
        ax.set_axis_off()
        fig.show()