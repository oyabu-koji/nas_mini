import hashlib
import json

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.detector_capability import (
    PHASE2B_MIGRATION_VERSION,
    evaluate_detector_capability,
)
from app.services.phase2b_migration import apply_phase2b_migration
from tests.detector_test_support import write_detector_artifacts
from app.services.detector_manifest import canonical_document, document_with_digest


def settings_for(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def test_missing_production_artifacts_keep_phase2a_mode(tmp_path):
    settings = settings_for(tmp_path)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)

    result = evaluate_detector_capability(settings)

    assert result.mode == "phase2a_compatibility"
    assert result.detector_certified is False
    assert result.formal_apple_log_preview is False


def test_v1_only_detector_artifacts_cannot_enable_v2_runtime(tmp_path):
    settings = settings_for(tmp_path)
    settings.detector_root.mkdir(parents=True)
    (settings.detector_root / "detector-rule-input-v1.json").write_bytes(b"{}")
    (settings.detector_root / "manifest.json").write_bytes(
        canonical_document({"schema_version": 1})
    )
    (settings.detector_root / "certificate-summary.json").write_bytes(
        canonical_document({"schema_version": 1})
    )
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)

    result = evaluate_detector_capability(settings)

    assert result.detector_certified is False
    assert result.formal_apple_log_preview is False
    assert result.blocked_reason == "log_detector_manifest_invalid"


def test_certified_detector_requires_explicit_detector_v2_marker(
    monkeypatch, tmp_path
):
    settings = settings_for(tmp_path)
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.detector_capability.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)

    before_marker = evaluate_detector_capability(settings)
    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=lambda _settings: None,
    )
    after_marker = evaluate_detector_capability(settings)

    assert before_marker.detector_certified is True
    assert before_marker.formal_apple_log_preview is False
    assert after_marker.mode == "phase2a_compatibility"
    assert after_marker.formal_apple_log_preview is False
    assert after_marker.blocked_reason == "detector_v2_migration_not_applied"


def test_invalid_manifest_keeps_detector_and_formal_capabilities_false(tmp_path):
    settings = settings_for(tmp_path)
    write_detector_artifacts(settings.detector_root)
    (settings.detector_root / "manifest.json").write_bytes(b"{}")
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)

    result = evaluate_detector_capability(settings)

    assert result.detector_certified is False
    assert result.formal_apple_log_preview is False
    assert result.blocked_reason == "log_detector_manifest_invalid"


def test_runtime_ffprobe_version_mismatch_keeps_capabilities_false(
    monkeypatch, tmp_path
):
    settings = settings_for(tmp_path)
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.detector_capability.read_ffprobe_version",
        lambda **_kwargs: "ffprobe other build",
    )
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (PHASE2B_MIGRATION_VERSION,),
        )
        conn.commit()

    result = evaluate_detector_capability(settings)

    assert result.detector_certified is False
    assert result.formal_apple_log_preview is False
    assert result.blocked_reason == "log_detector_version_mismatch"


@pytest.mark.parametrize("mismatch", ["rule", "manifest", "summary", "parser"])
def test_v2_rule_manifest_summary_and_parser_mismatches_block_runtime(
    monkeypatch, tmp_path, mismatch
):
    settings = settings_for(tmp_path)
    rule_path, rule_value, manifest_value = write_detector_artifacts(
        settings.detector_root
    )
    monkeypatch.setattr(
        "app.services.detector_capability.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )
    if mismatch == "rule":
        rule_value["rule_version"] = "changed-v2"
        raw = canonical_document(rule_value)
        rule_path.write_bytes(raw)
        (rule_path.parent / f"{rule_path.name}.sha256").write_text(
            hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii"
        )
    elif mismatch == "manifest":
        manifest_value["show_entries"] += ":format_tags"
        (settings.detector_root / "manifest.json").write_bytes(
            document_with_digest(manifest_value, "manifest_sha256")
        )
    elif mismatch == "summary":
        summary_path = settings.detector_root / "certificate-summary.json"
        summary = json.loads(summary_path.read_bytes())
        summary["manifest_sha256"] = "0" * 64
        summary_path.write_bytes(canonical_document(summary))
    else:
        manifest_value["parser_contract_version"] = "iso-bmff-apple-log-v0"
        (settings.detector_root / "manifest.json").write_bytes(
            document_with_digest(manifest_value, "manifest_sha256")
        )
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)

    result = evaluate_detector_capability(settings)

    assert result.detector_certified is False
    assert result.formal_apple_log_preview is False
    assert result.blocked_reason == "log_detector_manifest_invalid"
