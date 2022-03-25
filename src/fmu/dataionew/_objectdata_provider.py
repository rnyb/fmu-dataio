"""Module for DataIO _ObjectData

This contains evaluation of the valid objects that can be handled and is mostly used
in the ``data`` block but some settings are applied later in the other blocks

Example data block::

data:

    # if stratigraphic, name must match the strat column; official name of this surface.
    name: volantis_top-volantis_base
    stratigraphic: false  # if true, this is a stratigraphic surf found in strat column
    offset: 0.0  # to be used if a specific horizon is represented with an offset.

    top: # not required, but allowed
        name: volantis_gp_top
        stratigraphic: true
        offset: 2.0
    base:
        name: volantis_gp_top
        stratigraphic: true
        offset: 8.3

    stratigraphic_alias: # other stratigraphic entities this corresponds to
                         # in the strat column, e.g. Top Viking vs Top Draupne.
        - SomeName Fm. 1 Top
    alias: # other known-as names, such as name used inside RMS etc
        - somename_fm_1_top
        - top_somename

    # content is white-listed and standardized
    content: depth

    # tagname is flexible. The tag is intended primarily for providing uniqueness.
    # The tagname will also be part of the outgoing file name on disk.
    tagname: ds_extract_geogrid

    # no content-specific attribute for "depth" but can come in the future

    properties: # what the values actually show. List, only one for IRAP Binary
                # surfaces. Multiple for 3d grid or multi-parameter surfaces.
                # First is geometry.
        - name: PropertyName
          attribute: owc
          is_discrete: false # to be used for discrete values in surfaces.
          calculation: null # max/min/rms/var/maxpos/sum/etc

    format: irap_binary
    layout: regular # / cornerpoint / structured / etc
    unit: m
    vertical_domain: depth # / time / null
    depth_reference: msl # / seabed / etc # mandatory when vertical_domain is depth?
    grid_model: # Making this an object to allow for expanding in the future
        name: MyGrid # important for data identification, also for other data types
    spec: # class/layout dependent, optional? Can spec be expanded to work for all
          # data types?
        ncol: 281
        nrow: 441
        ...
    bbox:
        xmin: 456012.5003497944
        xmax: 467540.52762886323
        ...
    time:
        - value: 2020-10-28T14:28:02
          label: "some label"
        - value: 2020-10-28T14:28:02
          label: "some other label"
    is_prediction: true # For separating pure QC output from actual predictions
    is_observation: true # Used for 4D data currently but also valid for other data?
    description:
        - Depth surfaces extracted from the structural model

"""
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# import pandas as pd
import xtgeo

from ._definitions import _ValidFormats
from ._utils import C, G, S

# from warnings import warn


# try:
#     import pyarrow as pa
# except ImportError:
#     HAS_PYARROW = False
# else:
#     HAS_PYARROW = True
#     from pyarrow import feather

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    pass


@dataclass
class _ObjectDataProvider:
    """Class for providing metadata for data objects in fmu-dataio, e.g. a surface.

    The metadata for the data are constructed by:

    * Investigating (parsing) the object (e.g. a XTGeo RegularSurface) itself
    * Combine the object info with settings from settings and globalconfig and classvar
    """

    # input fields, cannot be defaulted
    obj: Any
    cfg: dict

    # result properties; the most important is metadata which IS the 'data' part in
    # the resulting metadata. But other variables needed later are also given
    # as instance properties in addition (for simplicity in other classes/functions)
    metadata: dict = field(default_factory=dict)

    name: str = ""
    classname: str = ""
    efolder: str = ""
    fmt: str = ""
    extension: str = ""
    layout: str = ""
    bbox: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)

    def __post_init__(self):

        # more explicit:
        self.settings = self.cfg[S]
        self.globalconfig = self.cfg[G]
        self.classvar = self.cfg[C]
        logger.info("Ran __post_init__")

    def _derive_name_stratigraphy(self) -> dict:
        """Derive the name and stratigraphy for the object; may have several sources.

        If not in input settings it is tried to be inferred from the xtgeo/pandas/...
        object. The name is then checked towards the stratigraphy list, and name is
        replaced with official stratigraphic name if found in static metadata
        `stratigraphy`. For example, if "TopValysar" is the model name and the actual
        name is "Valysar Top Fm." that latter name will be used.

        """
        logger.info("Evaluate data:name attribute and stratigraphy")
        result = dict()  # shorter form

        name = self.settings.get("name", "")

        if not name:
            try:
                name = self.obj.name
            except AttributeError:
                name = ""

        # next check if usename has a "truename" and/or aliases from the config
        strat = self.globalconfig.get("stratigraphy", None)  # shortform

        if strat is None or name not in strat:
            logger.info("None of name not in strat")
            result["stratigraphic"] = False
            result["name"] = name
        else:
            logger.info("The name in strat...")
            result["name"] = strat[name].get("name", name)
            result["alias"] = strat[name].get("alias", list())
            if result["name"] != "name":
                result["alias"].append(name)
            result["stratigraphic"] = strat[name].get("stratigraphic", False)
            result["stratigraphic_alias"] = strat[name].get("stratigraphic_alias", None)
            result["offset"] = strat[name].get("offset", None)
            result["top"] = strat[name].get("top", None)
            result["base"] = strat[name].get("base", None)

        logger.info("Evaluated data:name attribute, true name is <%s>", result["name"])
        return result

    @staticmethod
    def _validate_get_ext(fmt, subtype, validator):
        """Validate that fmt (file format) matches data and return legal extension."""
        if fmt not in validator.keys():
            raise ConfigurationError(
                f"The file format {fmt} is not supported.",
                f"Valid {subtype} formats are: {list(validator.keys())}",
            )

        ext = validator.get(fmt, None)
        return ext

    def _derive_objectdata(self):
        """Derive object spesific data."""
        logger.info("Evaluate data settings for object")
        result = dict()

        if isinstance(self.obj, xtgeo.RegularSurface):
            result["subtype"] = "RegularSurface"
            result["classname"] = "surface"
            result["layout"] = "regular"
            result["efolder"] = "maps"
            result["fmt"] = self.classvar["surface_fformat"]
            result["extension"] = self._validate_get_ext(
                result["fmt"], result["subtype"], _ValidFormats().surface
            )
            result["spec"], result["bbox"] = self._derive_spec_bbox_regularsurface()
        # elif isinstance(self.obj, xtgeo.Polygons):
        #     result["subtype"] = "Polygons"
        #     result["classname"] = "polygons"
        #     result["efolder"] = "polygons"
        #     # TODO! selv.layout
        #     result["extension"] = self._validate_get_ext(VALID_POLYGONS_FORMATS)
        # elif isinstance(self.obj, xtgeo.Points):
        #     result["subtype"] = "Points"
        #     result["classname"] = "points"
        #     # TODO! selv.layout
        #     result["efolder"] = "points"
        #     result["extension"] = self._validate_get_ext(VALID_POINTS_FORMATS)
        # elif isinstance(self.obj, xtgeo.Cube):
        #     result["subtype"] = "RegularCube"
        #     result["classname"] = "cube"
        #     # TODO! selv.layout
        #     result["efolder"] = "cubes"
        #     result["extension"] = self._validate_get_ext(VALID_CUBE_FORMATS)
        # elif isinstance(self.obj, xtgeo.Grid):
        #     result["subtype"] = "CPGrid"
        #     result["classname"] = "cpgrid"
        #     # TODO! selv.layout
        #     result["efolder"] = "grids"
        #     result["extension"] = self._validate_get_ext(VALID_GRID_FORMATS)
        # elif isinstance(self.obj, xtgeo.GridProperty):
        #     result["subtype"] = "CPGridProperty"
        #     result["classname"] = "cpgrid_property"
        #     # TODO! selv.layout
        #     result["efolder"] = "grids"
        #     result["extension"] = self._validate_get_ext(VALID_GRID_FORMATS)
        # elif isinstance(self.obj, pd.DataFrame):
        #     result["subtype"] = "DataFrame"
        #     result["classname"] = "table"
        #     # TODO! selv.layout
        #     result["efolder"] = "tables"
        #     result["extension"] = self._validate_get_ext(VALID_TABLE_FORMATS)
        # elif HAS_PYARROW and isinstance(self.obj, pa.Table):
        #     result["subtype"] = "ArrowTable"
        #     result["classname"] = "table"
        #     # TODO! selv.layout
        #     result["efolder"] = "tables"
        #     result["extension"] = self._validate_get_ext(VALID_TABLE_FORMATS)
        # else:
        #     raise NotImplementedError(
        #         "This data type is not (yet) supported: ", type(self.obj)
        #     )

        return result

    def _derive_spec_bbox_regularsurface(self):
        """Process/collect the data.spec and data.bbox for RegularSurface"""
        logger.info("Derive bbox and specs for RegularSurface")

        specs = dict()

        regsurf = self.obj
        xtgeo_specs = regsurf.metadata.required
        for spec, val in xtgeo_specs.items():
            if isinstance(val, (np.float32, np.float64)):
                val = float(val)
            specs[spec] = val
        specs["undef"] = 1.0e30  # irap binary undef

        bbox = dict()
        bbox["xmin"] = float(regsurf.xmin)
        bbox["xmax"] = float(regsurf.xmax)
        bbox["ymin"] = float(regsurf.ymin)
        bbox["ymax"] = float(regsurf.ymax)
        bbox["zmin"] = float(regsurf.values.min())
        bbox["zmax"] = float(regsurf.values.max())

        return specs, bbox

    def derive_metadata(self):
        """Main function here, will populate the metadata block for 'data'."""
        logger.info("Derive all metadata for data object")
        nameres = self._derive_name_stratigraphy()
        objres = self._derive_objectdata()

        meta = self.metadata  # shortform

        meta["name"] = nameres["name"]
        meta["stratigraphic"] = nameres.get("stratigraphic", None)
        meta["offset"] = nameres.get("offset", None)
        meta["alias"] = nameres.get("alias", None)
        meta["top"] = nameres.get("top", None)
        meta["base"] = nameres.get("base", None)
        meta["content"] = self.settings.get("content", None)
        meta["tagname"] = self.settings.get("tagname", None)
        meta["format"] = objres["fmt"]
        meta["layout"] = objres["layout"]
        meta["unit"] = self.settings.get("unit", None)
        meta["vertical_domain"] = self.settings.get("vertical_domain", None)
        meta["depth_reference"] = self.settings.get("depth_reference", None)
        meta["spec"] = objres["spec"]
        meta["bbox"] = objres["bbox"]
        meta["time"] = self.settings.get("time", None)
        meta["is_prediction"] = self.settings.get("is_prediction", False)
        meta["is_observation"] = self.settings.get("is_observation", False)
        meta["description"] = self.settings.get("description", None)

        # the next is to give addition state variables identical values, and for
        # consistency these are derived after all eventual validation and directly from
        # the self.metadata fields:

        self.name = meta["name"]

        # then there are a few settings that are not in the ``data`` metadata, but
        # needed as data/variables in other classes:

        self.efolder = objres["efolder"]
        self.classname = objres["classname"]
        self.extension = objres["extension"]
        self.fmt = objres["fmt"]