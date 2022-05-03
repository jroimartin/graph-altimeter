"""This modules allows to run Altimeter scans in the context of the Security
Graph."""

from datetime import datetime
import logging
import uuid

from gremlin_python.process.anonymous_traversal import traversal
from altimeter.aws.resource.support.severity_level import (
    SeverityLevelResourceSpec
)
from altimeter.aws.resource_service_region_mapping import (
    build_aws_resource_region_mapping_repo
)
from altimeter.aws.scan.muxer.local_muxer import LocalAWSScanMuxer
from altimeter.aws.scan.scan import run_scan
from altimeter.aws.scan.settings import ALL_RESOURCE_SPEC_CLASSES
from altimeter.core.artifact_io.reader import ArtifactReader
from altimeter.core.artifact_io.writer import ArtifactWriter
from altimeter.core.neptune.client import NeptuneEndpoint

from graph_altimeter import InvalidRoleArnError, CURRENT_UNIVERSE
from graph_altimeter.dsl import AltimeterTraversalSource
from graph_altimeter.scan.neptune_client import GraphAltimeterNeptuneClient
from graph_altimeter.scan.postprocess import postprocess


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def run(config, account_id, resource_specs=None):
    # pylint: disable=too-many-locals
    """Given an Altimeter ``config``, runs an Altimeter scan and stores the
    result in a Gremlin compatible DB following the Altimeter Universe
    schema."""
    endpoint = NeptuneEndpoint(
        host=config.neptune.host,
        port=config.neptune.port,
        region=config.neptune.region,
        ssl=bool(config.neptune.ssl),
        auth_mode=str(config.neptune.auth_mode),
    )

    # We exclude from the scan the support resources.
    if resource_specs is None:
        resource_specs = tuple(
            spec for spec
            in ALL_RESOURCE_SPEC_CLASSES
            if spec != SeverityLevelResourceSpec
        )

    scan_id = generate_scan_id()
    muxer = LocalAWSScanMuxer(
        scan_id=scan_id,
        config=config,
        resource_spec_classes=resource_specs
    )

    preferred_account_scan_regions = config.scan.preferred_account_scan_regions
    resources_regions = build_aws_resource_region_mapping_repo(
        global_region_whitelist=config.scan.regions,
        preferred_account_scan_regions=preferred_account_scan_regions,
        services_regions_json_url=config.services_regions_json_url,
    )

    artifact_reader = ArtifactReader.from_artifact_path(config.artifact_path)
    artifact_writer = ArtifactWriter.from_artifact_path(
        artifact_path=config.artifact_path,
        scan_id=scan_id
    )

    manifest, graph_set = run_scan(
        muxer=muxer,
        config=config,
        aws_resource_region_mapping_repo=resources_regions,
        artifact_writer=artifact_writer,
        artifact_reader=artifact_reader,
    )

    scanned_accounts = manifest.scanned_accounts
    not_scanned_accounts = manifest.unscanned_accounts
    logger.debug(
        "finished scan %s, \
        scanned accounts: %s, \
        not scanned accounts: %s",
        scan_id,
        scanned_accounts,
        not_scanned_accounts,
    )
    neptune_client = GraphAltimeterNeptuneClient(endpoint)

    logger.debug('scan %s: writing results to Gremlin DB', scan_id)
    graph = graph_set.to_neptune_lpg(scan_id)
    postprocess(graph, scan_id, account_id)
    snapshot_vertex = add_snapshot_vertex(neptune_client, scan_id)
    neptune_client.write_to_neptune_lpg(graph, scan_id, snapshot_vertex)
    logger.debug('scan %s: finished writting results to Gremlin DB', scan_id)


def add_snapshot_vertex(neptune_client, scan_id):
    """Creates a Altimeter snapshot vertex and links it to the proper universe. It
    also ensures that the current ``Universe`` vertex exists. Returns
    the vertex of the snapshot."""
    # The vertex_id of the snapshot must follow a concrete naming convention.
    vertex_id = f"altimeter_snapshot_{scan_id}"
    _, conn = neptune_client.connect_to_gremlin()
    try:
        g = traversal(AltimeterTraversalSource).withRemote(conn)
        g.ensure_universe(CURRENT_UNIVERSE).next()
        vertex = g.add_snapshot(vertex_id, CURRENT_UNIVERSE).next()
    finally:
        conn.close()
    return vertex


def parse_role_arn(arn):
    """Parses a role arn and returns the account and role name parts."""
    parts = arn.split(":")
    if len(parts) != 6:
        raise InvalidRoleArnError(arn)
    account_id = parts[4]
    role_name = parts[5].replace("role/", "")
    return (account_id, role_name)


def generate_scan_id():
    """Generates a unique scan id."""
    now = datetime.now()
    scan_date = now.strftime("%Y%m%d")
    scan_time = str(int(now.timestamp()))
    scan_id = "/".join((scan_date, scan_time, str(uuid.uuid4())))
    return scan_id
