"""This modules defines the postprocessing done to the graph generated by
Altimeter."""

from altimeter.core.neptune.client import AltimeterNeptuneClient

from graph_altimeter.scan.iam import expand_iam_policies


def postprocess(graph_dict, scan_id):
    """Postprocess in place the dictionary returned by Altimeter as
    a result of a scan so it matches the expected schema of the Altimeter
    Universe."""
    expand_iam_policies(graph_dict)
    _fix_orphan_edges(graph_dict, scan_id)


def _create_vertex(v_id, scan_id):
    """Returns an Altimeter vertex given a vertex ID and a scan ID."""
    label = AltimeterNeptuneClient.parse_arn(v_id)["resource"]
    new_vertex = {
        "~id": v_id,
        "~label": label,
        "scan_id": scan_id,
        "arn": str(v_id),
    }
    return new_vertex


def _fix_orphan_edges(graph_dict, scan_id):
    """Finds the edges with non existent in or out vertices."""
    vertices = graph_dict["vertices"]
    edges = graph_dict["edges"]

    existing_vertices = {v["~id"] for v in vertices}
    non_existing_vertices = {}
    for e in edges:
        from_vid = e["~from"]
        to_vid = e["~to"]
        if ((from_vid not in existing_vertices) and
           (from_vid not in non_existing_vertices)):
            non_existing_vertices[from_vid] = _create_vertex(from_vid, scan_id)
        if ((to_vid not in existing_vertices) and
           (to_vid not in non_existing_vertices)):
            non_existing_vertices[to_vid] = _create_vertex(to_vid, scan_id)

    vertices = vertices + list(non_existing_vertices.values())
    graph_dict["vertices"] = vertices