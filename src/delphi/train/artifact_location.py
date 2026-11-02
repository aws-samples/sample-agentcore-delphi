"""Where the router artifact lives — the publisher's single source of destinations
(contract C6 as amended; SD-9; NFR10.7).

`config/artifact-location.json` is the ONE location definition. The CDK stack
(infra/delphi_infra/runtime_stack.py) reads the same file with the standard library's
`json` to compose the router arm's DELPHI_ARTIFACT_URI, and T-PKG-U3-20 asserts the two
sides resolve the same destinations. JSON rather than YAML because the CDK venv has no
PyYAML (M5). The file holds placeholders only — never an account id.

Two forms (SD-8):
  * legacy_prefix — the flat `router/current/` triple the live router reads today;
  * pointer       — `router/current.json`, naming an immutable `router/<artifact_hash>/`.
`serving_form` records which one the stack emits. U3 ships `legacy_prefix`; the flip to
`pointer` is U6's, after `delphi-artifact verify-remote` exits 0 (SD-10).

Offline/publication-side module: the request path does not import it.
"""
from __future__ import annotations

import json
import pathlib
import posixpath
from dataclasses import dataclass

from delphi.config import _resolve_config_dir

LOCATION_FILE = "artifact-location.json"
SERVING_FORMS = ("legacy_prefix", "pointer")
_KEYS = ("bucket", "pointer_key", "triple_prefix", "legacy_prefix", "serving_form")


@dataclass(frozen=True)
class ArtifactLocation:
    bucket_template: str
    pointer_key: str
    triple_prefix_template: str
    legacy_prefix: str
    serving_form: str

    def bucket(self, account: str, region: str) -> str:
        return self.bucket_template.format(account=account, region=region)

    def triple_prefix(self, artifact_hash: str) -> str:
        return self.triple_prefix_template.format(artifact_hash=artifact_hash)

    @property
    def serving_key(self) -> str:
        return self.legacy_prefix if self.serving_form == "legacy_prefix" else self.pointer_key

    def serving_uri(self, account: str, region: str) -> str:
        """The DELPHI_ARTIFACT_URI the stack emits for the router arm."""
        return f"s3://{self.bucket(account, region)}/{self.serving_key}"

    def pointer_uri(self, account: str, region: str) -> str:
        return f"s3://{self.bucket(account, region)}/{self.pointer_key}"


def load_location(path: str | pathlib.Path | None = None) -> ArtifactLocation:
    """Read and validate the location file (default: the resolved config directory)."""
    p = pathlib.Path(path) if path is not None else _resolve_config_dir() / LOCATION_FILE
    data = json.loads(p.read_text())
    missing = [k for k in _KEYS if k not in data]
    if missing:
        raise ValueError(f"{p}: missing keys {missing}")
    if data["serving_form"] not in SERVING_FORMS:
        raise ValueError(f"{p}: serving_form {data['serving_form']!r} not in {SERVING_FORMS}")
    if "{artifact_hash}" not in data["triple_prefix"] or not data["triple_prefix"].endswith("/"):
        raise ValueError(f"{p}: triple_prefix must be a '{{artifact_hash}}/' prefix")
    if not data["pointer_key"].endswith(".json"):
        raise ValueError(f"{p}: pointer_key must end in .json (the loader reads the form)")
    parent = posixpath.dirname(data["pointer_key"])
    beside = f"{parent}/{{artifact_hash}}/" if parent else "{artifact_hash}/"
    if data["triple_prefix"] != beside:
        # The cold-start loader requires the pointer to name the hashed prefix beside it.
        raise ValueError(f"{p}: triple_prefix {data['triple_prefix']!r} must be {beside!r}, "
                         f"the prefix beside pointer_key, or the loader refuses the pointer")
    return ArtifactLocation(
        bucket_template=data["bucket"],
        pointer_key=data["pointer_key"],
        triple_prefix_template=data["triple_prefix"],
        legacy_prefix=data["legacy_prefix"],
        serving_form=data["serving_form"],
    )


__all__ = ["ArtifactLocation", "LOCATION_FILE", "SERVING_FORMS", "load_location"]
