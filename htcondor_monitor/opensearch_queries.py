"""
opensearch_queries.py — low-level OpenSearch query functions.

Pure data retrieval with no threshold logic.  These are regular Python
functions (not smolagents tools) called directly by the metrics layer.
The smolagents tools in opensearch_tools.py are thin wrappers around these
for use cases where the agent still needs direct query access.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from opensearchpy import OpenSearch, RequestsHttpConnection

from htcondor_monitor.config.settings import settings

logger = logging.getLogger(__name__)


# ── Client and index helpers ──────────────────────────────────────────────────

def get_client() -> OpenSearch:
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


def index_pattern() -> str:
    return f"{settings.opensearch_index_prefix}-*"


def epoch_range(hours_back: int) -> tuple[int, int]:
    now = int(datetime.now(timezone.utc).timestamp())
    return now - hours_back * 3600, now


def kw(field: str) -> str:
    """Append .keyword suffix if the index mapping requires it."""
    return f"{field}.keyword" if settings.opensearch_use_keyword_suffix else field


# ── Raw query helpers ─────────────────────────────────────────────────────────

def search(body: dict) -> dict:
    """Execute a query and return the raw OpenSearch response."""
    client = get_client()
    try:
        return client.search(index=index_pattern(), body=body)
    except Exception as exc:
        logger.error("OpenSearch query failed: %s", exc)
        raise


def get_field_names() -> list[str]:
    client = get_client()
    mapping = client.indices.get_mapping(index=index_pattern())
    fields: set[str] = set()
    for idx_data in mapping.values():
        props = idx_data.get("mappings", {}).get("properties", {})
        fields.update(props.keys())
    return sorted(fields)


def get_schema_sample(num_docs: int = 3) -> list[dict]:
    resp = search({
        "size": min(num_docs, 10),
        "query": {"match_all": {}},
        "sort": [{settings.field_submit_time: {"order": "desc"}}],
    })
    return [h["_source"] for h in resp["hits"]["hits"]]


# ── Purpose-built query functions ─────────────────────────────────────────────

def fetch_jobs(
    hours_back: int = 24,
    time_field: str | None = None,
    job_status: list[int] | None = None,
    user: str | None = None,
    size: int = 500,
    extra_filters: list[dict] | None = None,
) -> list[dict]:
    """Return raw job ClassAd dicts matching the given filters."""
    field = time_field or settings.field_submit_time
    start_epoch, end_epoch = epoch_range(hours_back)

    must: list[dict] = [
        {"range": {field: {"gte": start_epoch, "lte": end_epoch}}}
    ]
    if job_status:
        must.append({"terms": {settings.field_job_status: job_status}})
    if user:
        must.append({"term": {kw(settings.field_user): user}})
    if extra_filters:
        must.extend(extra_filters)

    resp = search({
        "query": {"bool": {"must": must}},
        "size": min(size, settings.opensearch_max_results),
    })
    return [h["_source"] for h in resp["hits"]["hits"]]


def fetch_user_aggregations(
    hours_back: int = 24,
    time_field: str | None = None,
    extra_aggs: dict | None = None,
) -> list[dict]:
    """
    Return per-user aggregated statistics.  Includes percentile aggregations
    for memory and CPU so the metrics layer can compute distributions without
    a second query.
    """
    field = time_field or settings.field_submit_time
    start_epoch, _ = epoch_range(hours_back)
    F = settings

    aggs: dict = {
        "avg_cpu_user":        {"avg":          {"field": F.field_cpu_user}},
        "avg_wall_time":       {"avg":          {"field": F.field_wall_time}},
        "avg_memory_request":  {"avg":          {"field": F.field_memory_request}},
        "avg_memory_usage_kb": {"avg":          {"field": F.field_memory_usage}},
        "max_memory_usage_kb": {"max":          {"field": F.field_memory_usage}},
        "p90_memory_usage_kb": {"percentiles":  {"field": F.field_memory_usage, "percents": [90]}},
        "avg_disk_request":    {"avg":          {"field": F.field_disk_request}},
        "avg_disk_usage":      {"avg":          {"field": F.field_disk_usage}},
        "p90_cpu_user":        {"percentiles":  {"field": F.field_cpu_user, "percents": [90]}},
        "p90_wall_time":       {"percentiles":  {"field": F.field_wall_time, "percents": [90]}},
        "held_jobs":           {"filter":       {"term": {F.field_job_status: 5}}},
        "removed_jobs":        {"filter":       {"term": {F.field_job_status: 3}}},
        "completed_jobs":      {"filter":       {"term": {F.field_job_status: 4}}},
        "total_job_starts":    {"sum":          {"field": F.field_num_job_starts}},
        "shadow_exceptions":   {"sum":          {"field": F.field_num_shadow_exceptions}},
    }
    if extra_aggs:
        aggs.update(extra_aggs)

    resp = search({
        "size": 0,
        "query": {"range": {field: {"gte": start_epoch}}},
        "aggs": {
            "by_user": {
                "terms": {"field": kw(F.field_user), "size": 200},
                "aggs": aggs,
            }
        },
    })

    results = []
    for bucket in resp["aggregations"]["by_user"]["buckets"]:
        row: dict[str, Any] = {
            "user": bucket["key"],
            "job_count": bucket["doc_count"],
        }
        for key, val in bucket.items():
            if key in ("key", "doc_count"):
                continue
            if "values" in val:
                # percentiles agg — flatten to p90_field_name: value
                for pct_key, pct_val in val["values"].items():
                    row[f"{key}_p{pct_key.rstrip('0').rstrip('.')}"] = pct_val
            elif "value" in val:
                row[key] = val["value"]
            elif "doc_count" in val:
                row[key] = val["doc_count"]
        results.append(row)
    return results


def fetch_node_aggregations(
    hours_back: int = 168,
    time_field: str | None = None,
) -> list[dict]:
    """Return per-execute-node failure and eviction statistics."""
    field = time_field or settings.field_submit_time
    start_epoch, _ = epoch_range(hours_back)
    F = settings

    resp = search({
        "size": 0,
        "query": {"range": {field: {"gte": start_epoch}}},
        "aggs": {
            "by_node": {
                "terms": {"field": kw(F.field_last_remote_host), "size": 500},
                "aggs": {
                    "failed":            {"filter": {"terms": {F.field_job_status: [3, 5]}}},
                    "shadow_exceptions": {"sum":    {"field": F.field_num_shadow_exceptions}},
                    "total_job_starts":  {"sum":    {"field": F.field_num_job_starts}},
                    "exit_codes": {
                        "terms": {"field": F.field_exit_code, "size": 20}
                    },
                },
            }
        },
    })

    results = []
    for bucket in resp["aggregations"]["by_node"]["buckets"]:
        total = bucket["doc_count"]
        failed = bucket["failed"]["doc_count"]
        exit_codes = {
            b["key"]: b["doc_count"]
            for b in bucket["exit_codes"]["buckets"]
        }
        results.append({
            "node":              bucket["key"],
            "total_jobs":        total,
            "failed_jobs":       failed,
            "failure_rate_pct":  round(100 * failed / total, 1) if total else 0,
            "shadow_exceptions": bucket["shadow_exceptions"]["value"],
            "total_job_starts":  bucket["total_job_starts"]["value"],
            "exit_codes":        exit_codes,
        })
    return sorted(results, key=lambda r: r["failure_rate_pct"], reverse=True)


def fetch_hold_reasons(
    hours_back: int = 24,
    time_field: str | None = None,
) -> list[dict]:
    """Return a frequency table of HoldReason strings grouped by user."""
    field = time_field or settings.field_submit_time
    start_epoch, _ = epoch_range(hours_back)
    F = settings

    resp = search({
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"range": {field: {"gte": start_epoch}}},
                    {"term": {F.field_job_status: 5}},
                ]
            }
        },
        "aggs": {
            "by_reason": {
                "terms": {"field": kw(F.field_hold_reason), "size": 50},
                "aggs": {
                    "users": {"terms": {"field": kw(F.field_user), "size": 20}}
                },
            }
        },
    })

    return [
        {
            "hold_reason": bucket["key"],
            "count":       bucket["doc_count"],
            "users":       [u["key"] for u in bucket["users"]["buckets"]],
        }
        for bucket in resp["aggregations"]["by_reason"]["buckets"]
    ]


def fetch_fleet_percentiles(
    hours_back: int = 24,
    time_field: str | None = None,
) -> dict[str, float]:
    """
    Return fleet-wide percentile values for wall time and memory usage.
    Used by anomaly detection to establish baselines without per-user queries.
    """
    field = time_field or settings.field_submit_time
    start_epoch, _ = epoch_range(hours_back)
    F = settings

    resp = search({
        "size": 0,
        "query": {"range": {field: {"gte": start_epoch}}},
        "aggs": {
            "wall_time_pcts":  {"percentiles": {"field": F.field_wall_time,    "percents": [50, 75, 90, 95, 99]}},
            "memory_pcts":     {"percentiles": {"field": F.field_memory_usage,  "percents": [50, 75, 90, 95, 99]}},
            "job_starts_pcts": {"percentiles": {"field": F.field_num_job_starts,"percents": [50, 75, 90, 95, 99]}},
        },
    })

    def _flatten(agg_key: str, prefix: str) -> dict[str, float]:
        return {
            f"{prefix}_p{k.rstrip('0').rstrip('.')}": v
            for k, v in resp["aggregations"][agg_key]["values"].items()
        }

    return {
        **_flatten("wall_time_pcts",  "wall_time"),
        **_flatten("memory_pcts",     "memory_kb"),
        **_flatten("job_starts_pcts", "job_starts"),
    }
