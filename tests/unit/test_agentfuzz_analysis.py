"""Tests for the static analysis layer: contracts, sinks, graph, chains.

The negative cases matter as much as the positive ones.  Two are load-bearing:

* ``get_system_date`` calls ``subprocess.run`` with a constant argument.  If it
  ever becomes a sink, the sink scanner is matching on the API name and has
  stopped asking whether the argument is influenced.
* ``get_system_date -> publish_report`` is a ``TEXT``-to-``TEXT`` edge, which
  the graph must produce.  It is the false positive ``SPEC.md`` TC-02 names;
  the test records it rather than hiding it, so nobody mistakes the static
  graph for a curated one.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from agentfuzz import toolset
from agentfuzz.analysis import build_graph, extract_chains, extract_toolset, scan_sinks
from agentfuzz.analysis.chain_extractor import chains_for_sink_type
from agentfuzz.analysis.tool_extractor import ToolContractError
from agentfuzz.analysis.tool_extractor import extract_toolset as extract
from agentfuzz.contracts import DependencyKind, ResourceKind, SinkType, TypeTag


@pytest.fixture(scope="module")
def extraction():
    return extract_toolset(toolset)


@pytest.fixture(scope="module")
def sinks(extraction):
    names = tuple(contract.name for contract in extraction.contracts)
    return {sink.tool: sink for sink in scan_sinks(extraction.source_path, names)}


@pytest.fixture(scope="module")
def graph(extraction):
    return build_graph(extraction.contracts)


@pytest.fixture(scope="module")
def pool(graph, sinks):
    return extract_chains(graph, sinks.values())


def test_every_declared_tool_becomes_a_contract(extraction):
    assert len(extraction.contracts) == len(toolset.TOOL_METADATA)
    assert extraction.contracts[0].name == "search_docs"


def test_contract_carries_tags_and_resources(extraction):
    by_name = extraction.by_name()
    fetch = by_name["fetch_document"]
    assert fetch.param("url").tag is TypeTag.URL
    assert {field.name: field.tag for field in fetch.returns} == {
        "text": TypeTag.TEXT,
        "final_url": TypeTag.URL,
    }
    assert by_name["run_saved_script"].reads == (ResourceKind.WORKSPACE_FILE,)


def test_declared_metadata_must_match_the_signature():
    def smuggled(value: str) -> str:
        return value

    module = SimpleNamespace(
        __name__="fake.toolset",
        __file__="fake.py",
        smuggled=smuggled,
        TOOL_METADATA={
            "smuggled": {
                "summary": "mismatch",
                "params": {"other": TypeTag.TEXT},
                "returns": {"out": TypeTag.TEXT},
                "reads": (),
                "writes": (),
            }
        },
    )
    with pytest.raises(ToolContractError, match="do not match the signature"):
        extract(module)


def test_metadata_for_a_missing_callable_is_rejected():
    module = SimpleNamespace(
        __name__="fake.toolset",
        __file__="fake.py",
        TOOL_METADATA={
            "absent": {
                "summary": "missing",
                "params": {},
                "returns": {},
                "reads": (),
                "writes": (),
            }
        },
    )
    with pytest.raises(ToolContractError, match="no such callable"):
        extract(module)


def test_exactly_the_four_high_impact_tools_are_sinks(sinks):
    assert set(sinks) == {"fetch_document", "query_metrics", "run_saved_script", "run_shell"}
    assert sinks["fetch_document"].sink_types == (SinkType.SSRF,)
    assert sinks["query_metrics"].sink_types == (SinkType.SQLI,)
    assert sinks["run_saved_script"].sink_types == (SinkType.CMDI,)
    assert sinks["run_shell"].sink_types == (SinkType.CMDI,)


def test_constant_argument_is_never_a_sink(sinks):
    assert "get_system_date" not in sinks


def test_tools_without_high_impact_calls_are_not_sinks(sinks):
    for benign in ("summarize", "publish_report", "write_workspace_file", "fetch_document_2"):
        assert benign not in sinks


def test_sink_site_records_which_parameter_reached_it(sinks):
    site = sinks["run_saved_script"].sites[0]
    assert site.tainted_arguments == ("path",)
    assert sinks["run_shell"].sites[0].tainted_arguments == ("command",)
    assert sinks["fetch_document"].sites[0].api == "urllib.request.urlopen"


def test_taint_propagates_through_a_local_assignment(tmp_path):
    source = tmp_path / "helper_tool.py"
    source.write_text(
        textwrap.dedent(
            """
            import subprocess

            def run_wrapper(script: str) -> str:
                pieces = [script]
                completed = subprocess.run(pieces, capture_output=True, check=False)
                return completed.stdout
            """
        ),
        encoding="utf-8",
    )
    found = scan_sinks(source, ["run_wrapper"])
    assert [sink.tool for sink in found] == ["run_wrapper"]
    assert found[0].sites[0].tainted_arguments == ("script",)


def test_direct_edge_carries_the_tag_that_created_it(graph):
    edge = graph.edge_between("search_docs", "fetch_document")
    assert edge is not None
    assert edge.kind is DependencyKind.DIRECT
    assert edge.upstream_field == "results.url"
    assert edge.downstream_param == "url"


def test_persisted_artifact_is_recorded_on_the_edge(graph):
    edge = graph.edge_between("write_workspace_file", "run_saved_script")
    assert edge is not None
    assert [kind.value for kind in edge.shared_resources] == ["workspace_file"]


def test_incompatible_tags_produce_no_edge(graph):
    assert graph.edge_between("fetch_document", "query_metrics") is None
    assert graph.edge_between("export_csv", "run_shell") is None


def test_same_string_type_links_unrelated_tools(graph):
    """TC-02 false positive, asserted so it is never mistaken for a real dependency."""

    edge = graph.edge_between("get_system_date", "publish_report")
    assert edge is not None
    assert edge.rationale == "type tag 'text' passes through"


def test_chains_end_at_a_sink_and_are_multi_tool(pool, sinks):
    for chain in pool.chains:
        assert chain.tools[-1] == chain.sink
        assert chain.sink in sinks
        assert chain.length >= 2
        assert len(set(chain.tools)) == chain.length


def test_edges_align_with_the_tool_sequence(pool):
    for chain in pool.chains:
        assert len(chain.edges) == chain.length - 1
        for index, edge in enumerate(chain.edges):
            assert edge.upstream == chain.tools[index]
            assert edge.downstream == chain.tools[index + 1]


def test_two_step_ssrf_chain_is_found(pool):
    assert "search_docs->fetch_document" in {chain.identifier() for chain in pool.chains}


def test_cmdi_chain_passes_through_a_persisted_artifact(pool):
    identifiers = {chain.identifier() for chain in pool.chains}
    assert "write_workspace_file->run_saved_script" in identifiers
    assert "search_docs->fetch_document->write_workspace_file->run_saved_script" in identifiers


def test_sqli_chain_runs_through_the_query_builder(pool):
    identifiers = {chain.identifier() for chain in pool.chains}
    assert "search_docs->build_query->query_metrics" in identifiers


def test_all_three_sink_types_are_reachable_through_two_tools(pool):
    for sink_type in (SinkType.SSRF, SinkType.SQLI, SinkType.CMDI):
        chains = chains_for_sink_type(pool, sink_type)
        assert chains, f"no chain reaches {sink_type.value}"
        assert min(chain.length for chain in chains) == 2


def test_length_bound_is_respected(extraction, sinks):
    graph = build_graph(extraction.contracts)
    bounded = extract_chains(graph, sinks.values(), max_length=3)
    assert bounded.chains
    assert max(chain.length for chain in bounded.chains) == 3
    assert bounded.summary()["by_length"] == {"2": 4, "3": 21}


def test_chains_are_deterministic_and_deduplicated(extraction, sinks):
    graph = build_graph(extraction.contracts)
    first = extract_chains(graph, sinks.values(), max_length=3)
    second = extract_chains(graph, sinks.values(), max_length=3)
    assert [c.identifier() for c in first.chains] == [c.identifier() for c in second.chains]
    assert len({c.identifier() for c in first.chains}) == len(first.chains)


def test_min_length_below_two_is_refused(graph, sinks):
    with pytest.raises(ValueError, match="single-tool cases are out of scope"):
        extract_chains(graph, sinks.values(), min_length=1)
