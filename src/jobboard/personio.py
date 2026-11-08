"""Personio's public XML board feed, represented as provider fields in raw JSON."""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def board_slug(value: str) -> str:
    """A single DNS label, never a URL, path, userinfo or another hostname."""
    slug = value.strip().lower()
    if not SLUG.fullmatch(slug):
        raise ValueError(f"invalid Personio board slug: {value!r}")
    return slug


def feed_url(slug: str) -> str:
    return f"https://{board_slug(slug)}.jobs.personio.de/xml"


def _value(node: ET.Element):
    if len(node):
        return {child.tag: _value(child) for child in node}
    return (node.text or "").strip()


def parse_feed(body: bytes) -> dict:
    """Only a valid workzag-jobs feed can establish an empty board."""
    if b"<!DOCTYPE" in body.upper():
        raise ValueError("Personio feed contains a document type")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"invalid Personio XML: {exc}") from exc
    if root.tag != "workzag-jobs" or (root.text or "").strip():
        raise ValueError("not a Personio workzag-jobs feed")
    jobs, ids = [], set()
    for position in root:
        if position.tag != "position":
            raise ValueError(f"unexpected Personio feed element: {position.tag}")
        job = {child.tag: _value(child) for child in position}
        job["additionalOffices"] = [(n.text or "").strip() for n in position.findall("additionalOffices/office")]
        job["jobDescriptions"] = [{"name": (n.findtext("name") or "").strip(),
                                   "value": (n.findtext("value") or "").strip()}
                                  for n in position.findall("jobDescriptions/jobDescription")]
        native_id = job.get("id")
        if not isinstance(native_id, str) or not re.fullmatch(r"[0-9]+", native_id):
            raise ValueError("Personio position has no numeric ID")
        if not isinstance(job.get("name"), str) or not job["name"].strip():
            raise ValueError(f"Personio position {native_id} has no name")
        if native_id in ids:
            raise ValueError(f"duplicate Personio position ID: {native_id}")
        ids.add(native_id)
        jobs.append(job)
    return {"jobs": jobs}


def has_description(job: dict) -> bool:
    return any(section.get("value", "").strip() for section in job.get("jobDescriptions") or [])
