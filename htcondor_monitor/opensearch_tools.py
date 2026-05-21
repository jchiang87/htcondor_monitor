"""
smolagents Tool wrappers around OpenSearch queries.

Each tool is a self-contained callable that the CodeAgent can invoke.
Tools return plain Python dicts / lists so the agent can reason over them
directly in generated code without needing JSON parsing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from smolagents import tool
from opensearchpy import OpenSearch, RequestsHttpConnection

from .settings import settings

logger = logging.getLogger(__name__)


# ── Client factory ────────────────────────────────────────────────────────────

def _get_client() -> OpenSearch:
    """Return a configured OpenSearch client (created fresh per call — stateless)."""
    kwargs: dict[str, Any] = {
        "hosts": [settings.opensearch_host],
        "connection_class": RequestsHttpConnection,
        "timeout": settings.opensearch_timeout,
        "use_ssl": settings.opensearch_host.startswith("https"),
        "verify_certs": settings.opensearch_ca_cert is not None,
    }
    if settings.opensearch_ca_cert:
        kwargs["ca_certs"] = settings.opensearch_ca_cert
    if settings.opensearch_username:
        kwargs["http_auth"] = (settings.opensearch_username, settings.opensearch_password)
    return OpenSearch(**kwargs)


def _index_pattern() -> str:
    return f"{settings.opensearch_index_prefix}-*"


def _epoch_range(hours_back: int) -> tuple[int, int]:
    now = int(datetime.now(timezone.utc).timestamp())
    return now - hours_back * 3600, now


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def query_jobs(
    hours_back: int = 24,
    job_status: list[int] | None = None,
    user: str | None = None,
    size: int = 500,
    extra_filters: list[dict] | None = None,
) -> list[dict]:
    """
    Query HTCondor job ClassAds from OpenSearch.

    Args:
        hours_back: How many hours back to search (based on CompletionDate or QDate).
        job_status: List of HTCondor JobStatus codes to filter on.
                    4=Completed, 5=Held, 3=Removed, 1=Idle, 2=Running.
                    None means all statuses.
        user: Filter to a specific Owner (username).  None means all users.
        size: Maximum number of documents to return.
        extra_filters: Additional OpenSearch must-filters as raw dicts.

    Returns:
        List of job ClassAd dicts.
    """
    client = _get_client()
    start_epoch, end_epoch = _epoch_range(hours_back)

    must: list[dict] = [
        {"range": {settings.field_submit_time: {"gte": start_epoch, "lte": end_epoch}}}
    ]
    if job_status:
        must.append({"terms": {settings.field_job_status: job_status}})
    if user:
        must.append({"term": {f"{settings.field_user}.keyword": user}})
    if extra_filters:
        must.extend(extra_filters)

    query = {"query": {"bool": {"must": must}}, "size": min(size, settings.opensearch_max_results)}
    try:
        resp = client.search(index=_index_pattern(), body=query)
        return [hit["_source"] for hit in resp["hits"]["hits"]]
    except Exception as exc:
        logger.error("OpenSearch query failed: %s", exc)
        raise


@tool
def aggregate_by_user(
    hours_back: int = 24,
    metric_fields: list[str] | None = None,
) -> list[dict]:
    """
    Return per-user aggregated statistics over the given window.

    Aggregations include: job count, avg/max CPU efficiency, avg memory
    over-request ratio, hold count, eviction count, removal count.

    Args:
        hours_back: Lookback window in hours.
        metric_fields: Additional numeric fields to average (optional).

    Returns:
        List of dicts, one per user, with aggregated stats.
    """
    client = _get_client()
    start_epoch, _ = _epoch_range(hours_back)

    F = settings  # shorthand

    aggs: dict = {
        "jobs_total": {"value_count": {"field": F.field_cluster_id}},
        "avg_cpu_user": {"avg": {"field": F.field_cpu_user}},
        "avg_wall_time": {"avg": {"field": F.field_wall_time}},
        "avg_memory_request": {"avg": {"field": F.field_memory_request}},
        "avg_memory_usage_kb": {"avg": {"field": F.field_memory_usage}},
        "avg_disk_request": {"avg": {"field": F.field_disk_request}},
        "avg_disk_usage": {"avg": {"field": F.field_disk_usage}},
        "held_jobs": {"filter": {"term": {F.field_job_status: 5}}},
        "removed_jobs": {"filter": {"term": {F.field_job_status: 3}}},
        "completed_jobs": {"filter": {"term": {F.field_job_status: 4}}},
        "evictions": {"sum": {"field": F.field_num_job_starts}},
    }
    if metric_fields:
        for mf in metric_fields:
            aggs[f"avg_{mf}"] = {"avg": {"field": mf}}

    body = {
        "size": 0,
        "query": {
            "range": {F.field_submit_time: {"gte": start_epoch}}
        },
        "aggs": {
            "by_user": {
                "terms": {"field": f"{F.field_user}.keyword", "size": 200},
                "aggs": aggs,
            }
        },
    }
    resp = client.search(index=_index_pattern(), body=body)
    results = []
    for bucket in resp["aggregations"]["by_user"]["buckets"]:
        row = {"user": bucket["key"], "job_count": bucket["doc_count"]}
        for key, val in bucket.items():
            if key in ("key", "doc_count"):
                continue
            # value_count / filter aggs have "value" or "doc_count"
            if "value" in val:
                row[key] = val["value"]
            elif "doc_count" in val:
                row[key] = val["doc_count"]
        results.append(row)
    return results


@tool
def aggregate_by_node(hours_back: int = 168) -> list[dict]:
    """
    Return per-execute-node failure and eviction statistics.

    Args:
        hours_back: Lookback window in hours (default 7 days).

    Returns:
        List of dicts with node name, total jobs, failure count, failure rate.
    """
    client = _get_client()
    start_epoch, _ = _epoch_range(hours_back)
    F = settings

    body = {
        "size": 0,
        "query": {"range": {F.field_submit_time: {"gte": start_epoch}}},
        "aggs": {
            "by_node": {
                "terms": {"field": f"{F.field_last_remote_host}.keyword", "size": 500},
                "aggs": {
                    "failed": {
                        "filter": {
                            "terms": {F.field_job_status: [3, 5]}  # removed or held
                        }
                    },
                    "shadow_exceptions": {"sum": {"field": F.field_num_shadow_exceptions}},
                    "total_evictions": {"sum": {"field": F.field_num_job_starts}},
                },
            }
        },
    }
    resp = client.search(index=_index_pattern(), body=body)
    results = []
    for bucket in resp["aggregations"]["by_node"]["buckets"]:
        total = bucket["doc_count"]
        failed = bucket["failed"]["doc_count"]
        results.append({
            "node": bucket["key"],
            "total_jobs": total,
            "failed_jobs": failed,
            "failure_rate_pct": round(100 * failed / total, 1) if total else 0,
            "shadow_exceptions": bucket["shadow_exceptions"]["value"],
            "total_job_starts": bucket["total_evictions"]["value"],
        })
    return sorted(results, key=lambda r: r["failure_rate_pct"], reverse=True)


@tool
def get_hold_reason_summary(hours_back: int = 24) -> list[dict]:
    """
    Return a frequency table of HoldReason strings and codes.

    Args:
        hours_back: Lookback window in hours.

    Returns:
        List of dicts: {hold_reason, hold_reason_code, count, users}.
    """
    client = _get_client()
    start_epoch, _ = _epoch_range(hours_back)
    F = settings

    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"range": {F.field_submit_time: {"gte": start_epoch}}},
                    {"term": {F.field_job_status: 5}},
                ]
            }
        },
        "aggs": {
            "by_reason": {
                "terms": {"field": f"{F.field_hold_reason}.keyword", "size": 50},
                "aggs": {
                    "users": {"terms": {"field": f"{F.field_user}.keyword", "size": 20}}
                },
            }
        },
    }
    resp = client.search(index=_index_pattern(), body=body)
    results = []
    for bucket in resp["aggregations"]["by_reason"]["buckets"]:
        results.append({
            "hold_reason": bucket["key"],
            "count": bucket["doc_count"],
            "users": [u["key"] for u in bucket["users"]["buckets"]],
        })
    return results


@tool
def get_schema_sample(num_docs: int = 3) -> list[dict]:
    """
    Return a small sample of raw ClassAd documents from OpenSearch.
    Useful for the agent to discover available field names before querying.

    Args:
        num_docs: Number of sample documents to return (max 10).

    Returns:
        List of raw ClassAd dicts.
    """
    client = _get_client()
    resp = client.search(
        index=_index_pattern(),
        body={"size": min(num_docs, 10), "query": {"match_all": {}}},
    )
    return [hit["_source"] for hit in resp["hits"]["hits"]]


@tool
def get_index_field_names() -> list[str]:
    """
    Return all field names present in the HTCondor OpenSearch index mapping.
    Call this first to understand what ClassAd attributes are available.

    Returns:
        Sorted list of field name strings.
    """
    client = _get_client()
    mapping = client.indices.get_mapping(index=_index_pattern())
    fields: set[str] = set()
    for idx_data in mapping.values():
        props = idx_data.get("mappings", {}).get("properties", {})
        fields.update(props.keys())
    return sorted(fields)


@tool
def run_raw_query(query_body: str) -> dict:
    """
    Execute an arbitrary OpenSearch query (as a JSON string) and return results.
    Use this when the other tools don't cover your specific aggregation need.

    Args:
        query_body: A valid OpenSearch query DSL as a JSON string.

    Returns:
        The raw OpenSearch response dict (hits + aggregations if any).
    """
    client = _get_client()
    body = json.loads(query_body)
    resp = client.search(index=_index_pattern(), body=body)
    # Trim _source hits to avoid flooding context
    hits = [h["_source"] for h in resp.get("hits", {}).get("hits", [])]
    return {
        "total": resp["hits"]["total"]["value"],
        "hits": hits,
        "aggregations": resp.get("aggregations", {}),
    }


# ── Convenience: all tools as a list for agent registration ──────────────────

ALL_TOOLS = [
    query_jobs,
    aggregate_by_user,
    aggregate_by_node,
    get_hold_reason_summary,
    get_schema_sample,
    get_index_field_names,
    run_raw_query,
]
