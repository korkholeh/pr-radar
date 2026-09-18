from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import translation

from apps.metrics.docs import render_metrics_doc
from apps.metrics.registry import REGISTRY

METRICS_DOC_PATH = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "METRICS.md"


def test_committed_metrics_doc_is_fresh():
    committed = METRICS_DOC_PATH.read_text(encoding="utf-8")
    assert committed == render_metrics_doc(), "docs/METRICS.md is stale — run `manage.py metrics_doc`"


def test_every_registry_key_appears_in_the_document():
    text = render_metrics_doc()
    missing = [key for key in REGISTRY if f"`{key}`" not in text]
    assert not missing, f"docs/METRICS.md is missing metric key(s): {missing}"


def test_document_renders_in_english_while_the_active_language_is_uk():
    with translation.override("uk"):
        text = render_metrics_doc()
    assert "Pull requests merged on a day" in text


@pytest.mark.django_db
def test_check_flag_passes_when_the_committed_file_is_fresh(capsys):
    call_command("metrics_doc", "--check")
    assert "up to date" in capsys.readouterr().out


@pytest.mark.django_db
def test_check_flag_fails_on_a_stale_file(tmp_path, monkeypatch):
    import apps.metrics.management.commands.metrics_doc as metrics_doc_command

    stale_path = tmp_path / "METRICS.md"
    stale_path.write_text("stale content", encoding="utf-8")
    monkeypatch.setattr(metrics_doc_command, "METRICS_DOC_PATH", stale_path)

    with pytest.raises(SystemExit) as exc_info:
        call_command("metrics_doc", "--check")

    assert exc_info.value.code == 1


@pytest.mark.django_db
def test_command_writes_the_file(tmp_path, monkeypatch):
    import apps.metrics.management.commands.metrics_doc as metrics_doc_command

    target_path = tmp_path / "METRICS.md"
    monkeypatch.setattr(metrics_doc_command, "METRICS_DOC_PATH", target_path)

    call_command("metrics_doc")

    assert target_path.read_text(encoding="utf-8") == render_metrics_doc()
