"""Decode simulator certificates in both saved audit formats."""

from __future__ import annotations

import json
from pathlib import Path


def load_validator_indices(directory: Path) -> dict[int, str] | None:
    path = directory / "validator_indices.json"
    if not path.exists():
        return None
    entries = json.loads(path.read_text())
    addresses = {}
    for expected, entry in enumerate(entries):
        index = entry["index"]
        address = entry["address"]
        if type(index) is not int or index != expected or not isinstance(address, str):
            raise ValueError(f"invalid validator index manifest: {path}")
        addresses[index] = address
    roster = list(addresses.values())
    if roster != sorted(set(roster)):
        raise ValueError(f"duplicate or unordered validator addresses: {path}")
    return addresses


def decode_certificate_path(path: list, addresses: dict[int, str] | None) -> list[str]:
    if addresses is None:
        if all(isinstance(address, str) for address in path):
            return path
        raise ValueError("indexed certificate requires validator_indices.json")
    decoded = []
    for index in path:
        if type(index) is not int or index not in addresses:
            raise ValueError(f"unknown validator index in certificate: {index!r}")
        decoded.append(addresses[index])
    return decoded
