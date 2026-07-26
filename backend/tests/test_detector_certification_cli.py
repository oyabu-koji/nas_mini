from pathlib import Path

from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_certification import CertificationResult
from scripts import certify_apple_log_detector


def test_certification_cli_accepts_only_fixed_backend_command(tmp_path, monkeypatch, capsys):
    backend_root = Path(certify_apple_log_detector.__file__).resolve().parents[1]
    monkeypatch.chdir(backend_root)
    captured = {}

    def fake_certify(**kwargs):
        captured.update(kwargs)
        return CertificationResult(
            manifest_sha256="a" * 64,
            rule_input_sha256="b" * 64,
        )

    monkeypatch.setattr(certify_apple_log_detector, "certify_detector", fake_certify)

    result = certify_apple_log_detector.main(
        [
            "--rule-input",
            "assets/detectors/apple-log-v1/detector-rule-input-v1.json",
            "--fixture-root",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert captured["fixture_root"] == tmp_path
    assert str(tmp_path) not in capsys.readouterr().out


def test_certification_cli_rejects_alternate_rule_path(tmp_path, monkeypatch, capsys):
    backend_root = Path(certify_apple_log_detector.__file__).resolve().parents[1]
    monkeypatch.chdir(backend_root)

    result = certify_apple_log_detector.main(
        [
            "--rule-input",
            "alternate.json",
            "--fixture-root",
            str(tmp_path),
        ]
    )

    assert result == 2
    assert capsys.readouterr().err.strip() == "log_detector_manifest_invalid"


def test_certification_cli_failure_output_contains_only_stable_code(
    tmp_path, monkeypatch, capsys
):
    backend_root = Path(certify_apple_log_detector.__file__).resolve().parents[1]
    monkeypatch.chdir(backend_root)
    monkeypatch.setattr(
        certify_apple_log_detector,
        "certify_detector",
        lambda **_kwargs: (_ for _ in ()).throw(
            BoundedProcessError("log_probe_output_invalid")
        ),
    )

    result = certify_apple_log_detector.main(
        [
            "--rule-input",
            "assets/detectors/apple-log-v1/detector-rule-input-v1.json",
            "--fixture-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err.strip() == "log_probe_output_invalid"
    assert str(tmp_path) not in captured.err
